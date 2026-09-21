# =============================================================================
#  ChargeGrid Intelligence — Carteira NexusCoin
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Carteira NexusCoin — moeda interna do ChargeGrid, com paridade 1 NC = R$ 1,00.

Toda movimentação passa por `creditar` ou `debitar`, que gravam uma linha em
`transacoes`. O saldo é sempre reconciliável a partir do extrato, o que torna
o histórico auditável — requisito de qualquer módulo que toca faturamento.

Regras do produto:
    - pagar a recarga com NexusCoin devolve 10% do valor pago como NexusCoin
    - reservar um conector debita um sinal de R$ 10,00 no ato
    - o saldo nunca fica negativo: um débito acima do saldo levanta
      SaldoInsuficiente e não altera nada
"""

from __future__ import annotations

import db

# ---------------------------------------------------------------------------
# Constantes de negócio
# ---------------------------------------------------------------------------

CASHBACK_NEXUSCOIN: float = 0.10   # 10% de volta ao pagar com NexusCoin
SINAL_RESERVA_BRL:  float = 10.00  # pré-autorização da reserva de conector
RECARGA_MINIMA:     float = 1.00
RECARGA_MAXIMA:     float = 5000.00

# Tipos de transação aceitos (documentação viva do extrato)
TIPOS = ("RECARGA", "PAGAMENTO", "CASHBACK", "SINAL", "ESTORNO", "TAXA")


class SaldoInsuficiente(ValueError):
    """
    Levantada quando um débito excederia o saldo disponível.

    Carrega os dois números para que a interface possa dizer exatamente
    quanto falta, em vez de um genérico "saldo insuficiente".
    """

    def __init__(self, saldo: float, necessario: float) -> None:
        self.saldo = round(saldo, 2)
        self.necessario = round(necessario, 2)
        self.falta = round(necessario - saldo, 2)
        super().__init__(
            f"Faltam {self.falta:.2f} NC para esta operação "
            f"(saldo: {self.saldo:.2f} NC)."
        )


# ---------------------------------------------------------------------------
# Operações
# ---------------------------------------------------------------------------

def garantir_conta(usuario: str, saldo_inicial: float) -> None:
    """
    Cria a carteira do usuário no primeiro login.

    Idempotente: se a carteira já existe, o saldo atual é preservado — logar
    de novo não recarrega a conta.
    """
    db.execute(
        "INSERT OR IGNORE INTO carteira (usuario, saldo) VALUES (?, ?)",
        (usuario, round(saldo_inicial, 2)),
    )


def saldo(usuario: str) -> float:
    """Saldo atual em NexusCoin. Zero se a carteira não existir."""
    linha = db.query_one("SELECT saldo FROM carteira WHERE usuario = ?", (usuario,))
    return round(linha["saldo"], 2) if linha else 0.0


def creditar(usuario: str, valor: float, tipo: str, descricao: str = "") -> float:
    """
    Credita NexusCoin e registra a transação.

    Args:
        usuario   : chave da conta
        valor     : quantia positiva a creditar
        tipo      : um dos TIPOS (RECARGA, CASHBACK, ESTORNO…)
        descricao : texto exibido no extrato

    Returns:
        O novo saldo.

    Raises:
        ValueError : valor não positivo.
    """
    valor = round(float(valor), 2)
    if valor <= 0:
        raise ValueError("O valor a creditar deve ser positivo.")

    db.execute("UPDATE carteira SET saldo = ROUND(saldo + ?, 2) WHERE usuario = ?",
               (valor, usuario))
    db.execute(
        "INSERT INTO transacoes (usuario, tipo, valor, descricao) VALUES (?,?,?,?)",
        (usuario, tipo, valor, descricao),
    )
    return saldo(usuario)


def debitar(usuario: str, valor: float, tipo: str, descricao: str = "") -> float:
    """
    Debita NexusCoin e registra a transação.

    Args:
        usuario   : chave da conta
        valor     : quantia positiva a debitar
        tipo      : um dos TIPOS (PAGAMENTO, SINAL…)
        descricao : texto exibido no extrato

    Returns:
        O novo saldo.

    Raises:
        ValueError          : valor não positivo.
        SaldoInsuficiente   : o débito deixaria o saldo negativo (nada é alterado).
    """
    valor = round(float(valor), 2)
    if valor <= 0:
        raise ValueError("O valor a debitar deve ser positivo.")

    atual = saldo(usuario)
    if atual < valor:
        raise SaldoInsuficiente(atual, valor)

    db.execute("UPDATE carteira SET saldo = ROUND(saldo - ?, 2) WHERE usuario = ?",
               (valor, usuario))
    db.execute(
        "INSERT INTO transacoes (usuario, tipo, valor, descricao) VALUES (?,?,?,?)",
        (usuario, tipo, -valor, descricao),
    )
    return saldo(usuario)


def pode_pagar(usuario: str, valor: float) -> bool:
    """True se o saldo cobre o valor informado."""
    return saldo(usuario) >= round(float(valor), 2)


def extrato(usuario: str, limite: int = 25) -> list[dict]:
    """Últimas movimentações da carteira, mais recentes primeiro."""
    linhas = db.query_all(
        "SELECT tipo, valor, descricao, criado_em FROM transacoes "
        "WHERE usuario = ? ORDER BY id DESC LIMIT ?",
        (usuario, int(limite)),
    )
    return [dict(linha) for linha in linhas]


def total_cashback(usuario: str) -> float:
    """
    Soma de todo o cashback já recebido.

    Usado no recibo e na carteira para mostrar quanto o usuário economizou
    escolhendo NexusCoin em vez de Pix ou cartão.
    """
    linha = db.query_one(
        "SELECT COALESCE(SUM(valor), 0) AS total FROM transacoes "
        "WHERE usuario = ? AND tipo = 'CASHBACK'",
        (usuario,),
    )
    return round(linha["total"], 2) if linha else 0.0


if __name__ == "__main__":
    # Autoverificação sobre um banco temporário.
    import pathlib
    import tempfile

    db.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "teste.db"
    db.init()

    garantir_conta("teste", 10.0)
    garantir_conta("teste", 999.0)          # idempotente: não recarrega
    assert saldo("teste") == 10.0, "garantir_conta não pode sobrescrever saldo"

    assert creditar("teste", 5.0, "RECARGA", "recarga de teste") == 15.0
    assert debitar("teste", 3.0, "PAGAMENTO", "pagamento de teste") == 12.0

    try:
        debitar("teste", 100.0, "PAGAMENTO")
        raise AssertionError("deveria ter levantado SaldoInsuficiente")
    except SaldoInsuficiente as e:
        assert e.falta == 88.0, f"falta calculada errada: {e.falta}"
    assert saldo("teste") == 12.0, "débito recusado não pode alterar o saldo"

    assert len(extrato("teste")) == 2, "extrato deve ter as duas pontas"
    creditar("teste", 1.2, "CASHBACK", "cashback")
    assert total_cashback("teste") == 1.2
    print("wallet.py OK")
