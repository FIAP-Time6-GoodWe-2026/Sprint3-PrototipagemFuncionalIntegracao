# =============================================================================
#  ChargeGrid Intelligence — Reserva de Conector com Sinal
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Reserva de conector com pré-autorização (sinal).

O sinal de R$ 10,00 é debitado em NexusCoin no ato da reserva e tem dois
destinos possíveis:
    - o usuário comparece dentro do prazo → o sinal vira crédito e abate o
      valor da recarga
    - o prazo expira sem comparecimento   → o sinal é retido como taxa por
      bloqueio de conector

Cancelar dentro do prazo estorna integralmente: sem isso, o botão "Cancelar
reserva" da interface seria uma armadilha.

Expiração preguiçosa (lazy):
    Não há timer nem thread de fundo. Toda leitura do estado chama
    `expirar_vencidas()`, que marca as reservas com prazo vencido. Para uma
    instalação com dezenas de conectores isso é mais simples e mais barato que
    um agendador, e não perde eventos quando o processo reinicia.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Optional

import db
import wallet

logger = logging.getLogger(__name__)

DURACAO_MIN: int = 15
FMT: str = "%Y-%m-%d %H:%M:%S"

STATUS_ATIVA     = "ATIVA"
STATUS_USADA     = "USADA"
STATUS_EXPIRADA  = "EXPIRADA"
STATUS_CANCELADA = "CANCELADA"


@dataclass
class Reserva:
    """Uma reserva de conector, ativa ou já resolvida."""

    charger_id: str
    usuario:    str
    criada_em:  datetime.datetime
    expira_em:  datetime.datetime
    sinal_brl:  float
    status:     str

    @property
    def segundos_restantes(self) -> int:
        """Tempo até a expiração, nunca negativo."""
        delta = (self.expira_em - datetime.datetime.now()).total_seconds()
        return max(0, int(delta))

    @property
    def station_id(self) -> str:
        """Posto ao qual o conector reservado pertence (ex.: 'P1-C3' → 'P1')."""
        return self.charger_id.split("-")[0]


def _da_linha(linha) -> Reserva:
    """Converte uma linha do banco em Reserva."""
    return Reserva(
        charger_id=linha["charger_id"],
        usuario=linha["usuario"],
        criada_em=datetime.datetime.strptime(linha["criada_em"], FMT),
        expira_em=datetime.datetime.strptime(linha["expira_em"], FMT),
        sinal_brl=round(linha["sinal_brl"], 2),
        status=linha["status"],
    )


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

def expirar_vencidas() -> int:
    """
    Marca como EXPIRADA toda reserva ATIVA cujo prazo já passou.

    Returns:
        Quantidade de reservas expiradas nesta chamada.
    """
    agora = datetime.datetime.now().strftime(FMT)
    n = db.execute(
        "UPDATE reservas SET status = ? WHERE status = ? AND expira_em < ?",
        (STATUS_EXPIRADA, STATUS_ATIVA, agora),
    )
    if n:
        logger.info("Reservas expiradas: %d (sinal retido como taxa).", n)
    return n


def ativa_do_conector(charger_id: str) -> Optional[Reserva]:
    """Reserva ativa deste conector, se houver."""
    expirar_vencidas()
    linha = db.query_one(
        "SELECT * FROM reservas WHERE charger_id = ? AND status = ?",
        (charger_id, STATUS_ATIVA),
    )
    return _da_linha(linha) if linha else None


def ativa_do_usuario(usuario: str) -> Optional[Reserva]:
    """Reserva ativa deste usuário, se houver. Um usuário reserva um conector por vez."""
    expirar_vencidas()
    linha = db.query_one(
        "SELECT * FROM reservas WHERE usuario = ? AND status = ?",
        (usuario, STATUS_ATIVA),
    )
    return _da_linha(linha) if linha else None


def ativas_do_posto(posto_id: str) -> dict[str, Reserva]:
    """
    Reservas ativas de um posto, indexadas por charger_id.

    Usada pela camada web para (a) não anunciar como livre um conector
    reservado e (b) renderizar o card no estado correto.
    """
    expirar_vencidas()
    linhas = db.query_all(
        "SELECT * FROM reservas WHERE status = ? AND charger_id LIKE ?",
        (STATUS_ATIVA, f"{posto_id}-%"),
    )
    return {linha["charger_id"]: _da_linha(linha) for linha in linhas}


def todas_ativas() -> list[Reserva]:
    """Todas as reservas ativas do sistema (painel do operador)."""
    expirar_vencidas()
    linhas = db.query_all(
        "SELECT * FROM reservas WHERE status = ? ORDER BY expira_em", (STATUS_ATIVA,)
    )
    return [_da_linha(linha) for linha in linhas]


def receita_retida() -> float:
    """Soma dos sinais retidos por não comparecimento (receita do posto)."""
    linha = db.query_one(
        "SELECT COALESCE(SUM(sinal_brl), 0) AS total FROM reservas WHERE status = ?",
        (STATUS_EXPIRADA,),
    )
    return round(linha["total"], 2) if linha else 0.0


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def criar(usuario: str, charger_id: str) -> Reserva:
    """
    Cria uma reserva de 15 minutos, debitando o sinal em NexusCoin.

    O débito acontece antes da gravação: se a carteira recusar, nenhuma
    reserva é criada.

    Args:
        usuario    : chave da conta
        charger_id : conector no formato "P1-C3"

    Returns:
        A reserva criada.

    Raises:
        ValueError                : conector já reservado, ou usuário com reserva ativa
        wallet.SaldoInsuficiente  : saldo abaixo do sinal
    """
    expirar_vencidas()

    if ativa_do_conector(charger_id):
        raise ValueError("Este conector já está reservado.")

    existente = ativa_do_usuario(usuario)
    if existente:
        raise ValueError(
            f"Você já tem uma reserva ativa em {existente.charger_id}. "
            f"Cancele-a antes de reservar outro conector."
        )

    agora  = datetime.datetime.now()
    expira = agora + datetime.timedelta(minutes=DURACAO_MIN)

    wallet.debitar(
        usuario, wallet.SINAL_RESERVA_BRL, "SINAL",
        f"Sinal de reserva · {charger_id}",
    )

    db.execute(
        "INSERT INTO reservas "
        "(charger_id, usuario, criada_em, expira_em, sinal_brl, status) "
        "VALUES (?,?,?,?,?,?)",
        (charger_id, usuario, agora.strftime(FMT), expira.strftime(FMT),
         wallet.SINAL_RESERVA_BRL, STATUS_ATIVA),
    )
    logger.info("Reserva criada: %s por %s até %s", charger_id, usuario,
                expira.strftime("%H:%M:%S"))
    return Reserva(charger_id, usuario, agora, expira,
                   wallet.SINAL_RESERVA_BRL, STATUS_ATIVA)


def cancelar(usuario: str, charger_id: str) -> float:
    """
    Cancela uma reserva dentro do prazo e estorna o sinal integralmente.

    Returns:
        O valor estornado.

    Raises:
        ValueError : reserva inexistente ou de outro usuário.
    """
    reserva = ativa_do_conector(charger_id)
    if reserva is None or reserva.usuario != usuario:
        raise ValueError("Reserva não encontrada.")

    db.execute("UPDATE reservas SET status = ? WHERE charger_id = ? AND status = ?",
               (STATUS_CANCELADA, charger_id, STATUS_ATIVA))
    wallet.creditar(usuario, reserva.sinal_brl, "ESTORNO",
                    f"Estorno de reserva · {charger_id}")
    logger.info("Reserva cancelada e estornada: %s (%s)", charger_id, usuario)
    return reserva.sinal_brl


def consumir(usuario: str, charger_id: str) -> float:
    """
    Marca a reserva como USADA quando o usuário comparece.

    Chamada no momento em que a recarga é iniciada no conector reservado.
    Silenciosa quando não há reserva — iniciar sem reservar é o caso comum.

    Returns:
        Valor do sinal a abater no pagamento, ou 0.0 se não havia reserva.
    """
    reserva = ativa_do_conector(charger_id)
    if reserva is None or reserva.usuario != usuario:
        return 0.0

    db.execute("UPDATE reservas SET status = ? WHERE charger_id = ? AND status = ?",
               (STATUS_USADA, charger_id, STATUS_ATIVA))
    logger.info("Reserva honrada: %s (%s) — R$ %.2f viram crédito.",
                charger_id, usuario, reserva.sinal_brl)
    return reserva.sinal_brl


def sinal_creditado(usuario: str, charger_id: str) -> float:
    """
    Sinal de uma reserva já marcada como USADA neste conector.

    Permite reabrir a tela de pagamento sem perder o abatimento: `consumir`
    é idempotente do ponto de vista financeiro porque a segunda leitura vem
    por aqui.
    """
    linha = db.query_one(
        "SELECT sinal_brl FROM reservas WHERE charger_id = ? AND usuario = ? "
        "AND status = ? ORDER BY id DESC",
        (charger_id, usuario, STATUS_USADA),
    )
    return round(linha["sinal_brl"], 2) if linha else 0.0


if __name__ == "__main__":
    import pathlib
    import tempfile

    db.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "teste.db"
    db.init()
    wallet.garantir_conta("amanda", 100.0)
    wallet.garantir_conta("allan", 5.0)

    r = criar("amanda", "P2-C1")
    assert wallet.saldo("amanda") == 90.0, "sinal deve ser debitado"
    assert r.segundos_restantes > 890, "prazo deve ser ~15 min"
    assert "P2-C1" in ativas_do_posto("P2")

    try:
        criar("amanda", "P2-C2")
        raise AssertionError("segunda reserva do mesmo usuário deveria falhar")
    except ValueError:
        pass

    assert cancelar("amanda", "P2-C1") == 10.0
    assert wallet.saldo("amanda") == 100.0, "cancelamento deve estornar"

    try:
        criar("allan", "P2-C1")
        raise AssertionError("saldo de 5 NC não cobre o sinal de 10")
    except wallet.SaldoInsuficiente:
        pass
    assert wallet.saldo("allan") == 5.0, "recusa não pode alterar saldo"

    criar("amanda", "P3-C2")
    assert consumir("amanda", "P3-C2") == 10.0
    assert consumir("amanda", "P3-C2") == 0.0, "consumir duas vezes não duplica"
    assert sinal_creditado("amanda", "P3-C2") == 10.0
    assert wallet.saldo("amanda") == 90.0, "reserva honrada não estorna"

    # Expiração: força o vencimento e confere que não há estorno
    criar("amanda", "P1-C1")
    db.execute("UPDATE reservas SET expira_em = ? WHERE charger_id = ? AND status = ?",
               ("2020-01-01 00:00:00", "P1-C1", STATUS_ATIVA))
    assert expirar_vencidas() == 1
    assert ativa_do_conector("P1-C1") is None
    assert wallet.saldo("amanda") == 80.0, "expiração retém o sinal"
    assert receita_retida() == 10.0
    print("reservations.py OK")
