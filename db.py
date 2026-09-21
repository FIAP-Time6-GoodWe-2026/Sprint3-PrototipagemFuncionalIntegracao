# =============================================================================
#  ChargeGrid Intelligence — Persistência (SQLite / stdlib)
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Camada de persistência mínima usando apenas `sqlite3` da biblioteca padrão.

Guarda o que não pode desaparecer quando o processo Flask reinicia:
    carteira    — saldo de NexusCoin por usuário
    transacoes  — extrato (recarga, pagamento, cashback, sinal, taxa, estorno)
    reservas    — reservas de conector com sinal já cobrado
    sessoes     — histórico de sessões encerradas (relatório, CSV, análise)

`carregar_sessoes()` faz o caminho inverso do arquivamento: reconstrói as
linhas da tabela `sessoes` como objetos `ChargingSession`, para que o menu de
terminal (`menu.py`) opere sobre a mesma classe que a camada web usa.

Sessões ATIVAS continuam vivendo no SessionManager em memória: este módulo
não substitui o SessionManager, apenas arquiva o que já terminou.

Decisão de design — uma conexão nova por operação:
    SQLite abre um arquivo local em microssegundos. Abrir e fechar por chamada
    elimina qualquer problema de thread do servidor de desenvolvimento do
    Flask (cada request roda em uma thread diferente) sem precisar de
    `check_same_thread=False` nem de um pool de conexões.
"""

from __future__ import annotations

import datetime
import logging
import pathlib
import sqlite3
from typing import Any, Iterable, List, Optional

from models import ChargingSession, SessionStatus, UserType

logger = logging.getLogger(__name__)

# O arquivo do banco fica ao lado do código, para que `python app.py` funcione
# de qualquer diretório de trabalho.
DB_PATH: pathlib.Path = pathlib.Path(__file__).with_name("chargegrid.db")

SCHEMA: str = """
CREATE TABLE IF NOT EXISTS carteira (
    usuario TEXT PRIMARY KEY,
    saldo   REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transacoes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario   TEXT NOT NULL,
    tipo      TEXT NOT NULL,   -- RECARGA|PAGAMENTO|CASHBACK|SINAL|ESTORNO|TAXA
    valor     REAL NOT NULL,   -- positivo credita, negativo debita
    descricao TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Uma linha por reserva, não por conector (#B49). Com charger_id como
-- chave primária, reservar o mesmo conector de novo apagava a reserva
-- anterior — e com ela o sinal retido de quem não compareceu, que sumia do
-- relatório de receita do posto.
CREATE TABLE IF NOT EXISTS reservas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    charger_id TEXT NOT NULL,
    usuario    TEXT NOT NULL,
    criada_em  TEXT NOT NULL,
    expira_em  TEXT NOT NULL,
    sinal_brl  REAL NOT NULL,
    status     TEXT NOT NULL   -- ATIVA|USADA|EXPIRADA|CANCELADA
);

CREATE TABLE IF NOT EXISTS sessoes (
    session_id    TEXT PRIMARY KEY,
    usuario       TEXT NOT NULL DEFAULT '',
    charger_id    TEXT NOT NULL,
    station_id    TEXT NOT NULL,
    vehicle_id    TEXT NOT NULL,
    user_name     TEXT NOT NULL DEFAULT '',
    user_type     TEXT NOT NULL,
    inicio        TEXT NOT NULL,
    fim           TEXT NOT NULL,
    hora_inicio   INTEGER NOT NULL DEFAULT 0,
    duracao_min   REAL NOT NULL,
    potencia_kw   REAL NOT NULL,
    energia_kwh   REAL NOT NULL,
    tarifa_kwh    REAL NOT NULL,
    custo_brl     REAL NOT NULL,
    metodo_pagto  TEXT NOT NULL DEFAULT '',
    sinal_abatido REAL NOT NULL DEFAULT 0,
    cashback_nc   REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_transacoes_usuario ON transacoes (usuario, id DESC);
CREATE INDEX IF NOT EXISTS idx_reservas_status    ON reservas   (status, usuario);
-- O banco garante o que a regra de negócio promete: no máximo uma reserva
-- ATIVA por conector, mesmo com dois pedidos simultâneos.
CREATE UNIQUE INDEX IF NOT EXISTS ux_reservas_ativa
    ON reservas (charger_id) WHERE status = 'ATIVA';
"""


def _conn() -> sqlite3.Connection:
    """Abre uma conexão com `row_factory` que devolve linhas indexáveis por nome."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    """
    Cria as tabelas e índices caso ainda não existam.

    Idempotente: pode ser chamada a cada import do módulo `app` sem efeito
    colateral, inclusive nos reloads automáticos do Flask em modo debug.
    """
    with _conn() as conn:
        _migrar_reservas(conn)
        conn.executescript(SCHEMA)


def _migrar_reservas(conn: sqlite3.Connection) -> None:
    """
    Converte a tabela `reservas` antiga (charger_id como PK) para o schema com
    `id` próprio.

    `CREATE TABLE IF NOT EXISTS` não altera tabela existente: sem esta
    migração, um banco criado antes do #B49 continuaria sobrescrevendo o
    histórico de reservas em silêncio.
    """
    colunas = conn.execute("PRAGMA table_info(reservas)").fetchall()
    if not colunas or any(c["name"] == "id" for c in colunas):
        return   # tabela ainda não existe, ou já está no schema novo

    conn.execute("ALTER TABLE reservas RENAME TO reservas_antiga")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO reservas (charger_id, usuario, criada_em, expira_em,"
        " sinal_brl, status)"
        " SELECT charger_id, usuario, criada_em, expira_em, sinal_brl, status"
        " FROM reservas_antiga"
    )
    conn.execute("DROP TABLE reservas_antiga")


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """
    Executa um comando de escrita (INSERT/UPDATE/DELETE).

    Args:
        sql    : comando SQL com placeholders `?`
        params : valores para os placeholders

    Returns:
        Número de linhas afetadas.
    """
    with _conn() as conn:
        return conn.execute(sql, tuple(params)).rowcount


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    """Executa um SELECT e devolve a primeira linha, ou None."""
    with _conn() as conn:
        return conn.execute(sql, tuple(params)).fetchone()


def query_all(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    """Executa um SELECT e devolve todas as linhas."""
    with _conn() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def reset() -> None:
    """
    Apaga todo o conteúdo das tabelas, preservando o schema.

    Usado pelo atalho de demonstração e pelos testes. Não remove o arquivo,
    apenas esvazia — assim quem já tem uma conexão aberta não quebra.
    """
    with _conn() as conn:
        for tabela in ("transacoes", "carteira", "reservas", "sessoes"):
            conn.execute(f"DELETE FROM {tabela}")


if __name__ == "__main__":
    # Autoverificação: cria o banco, grava, lê e limpa.
    init()
    execute("INSERT OR REPLACE INTO carteira (usuario, saldo) VALUES (?, ?)",
            ("__teste__", 42.0))
    linha = query_one("SELECT saldo FROM carteira WHERE usuario = ?", ("__teste__",))
    assert linha is not None and linha["saldo"] == 42.0, "escrita/leitura falhou"
    execute("DELETE FROM carteira WHERE usuario = ?", ("__teste__",))
    assert query_one("SELECT 1 FROM carteira WHERE usuario = ?", ("__teste__",)) is None
    print(f"db.py OK — banco em {DB_PATH}")


# ---------------------------------------------------------------------------
# Reconstrução do histórico como objetos de domínio
# ---------------------------------------------------------------------------

def _para_datetime(texto: str, alternativa: datetime.datetime) -> datetime.datetime:
    """
    Converte o texto gravado no banco em datetime, sem levantar exceção.

    A tabela guarda datas como texto ISO ("2026-07-27 12:00:00"). Linhas
    antigas ou importadas de fora podem vir em formato diferente ou vazias —
    nesse caso devolve `alternativa` em vez de quebrar o carregamento inteiro
    por causa de um registro.
    """
    try:
        return datetime.datetime.fromisoformat(texto)
    except (TypeError, ValueError):
        return alternativa


def carregar_sessoes() -> List[ChargingSession]:
    """
    Reconstrói as sessões pagas do histórico como objetos `ChargingSession`.

    Ordena por data de início e numera sequencialmente a partir de 1: esse
    O `numero` é o ID curto que o menu aceita digitado e a chave da busca
    binária. Quem ordena aqui é o próprio SQLite, com ORDER BY: o que está
    sendo estabelecido é a numeração dos registros na entrada. As ordenações
    que o usuário pede no menu passam por `algoritmos.bubble_sort` e
    `algoritmos.insertion_sort`.

    **NUNCA escreve no banco.** O menu é ferramenta de leitura e análise; a
    sessão cadastrada à mão nele vive só na memória daquela execução.

    Returns:
        Lista de sessões, possivelmente vazia. Banco ausente, tabela ainda não
        criada ou arquivo corrompido devolvem **lista vazia** em vez de
        exceção: faturamento não pode derrubar o processo por causa de uma
        leitura. O menu abre vazio e o cadastro manual preenche.
    """
    try:
        linhas = query_all(
            "SELECT * FROM sessoes WHERE metodo_pagto <> ''"
            " ORDER BY inicio, session_id"
        )
    except sqlite3.Error as erro:
        logger.warning("Histórico indisponível (%s) — carregando vazio.", erro)
        return []

    sessoes: List[ChargingSession] = []
    for indice, linha in enumerate(linhas, start=1):
        try:
            inicio = _para_datetime(linha["inicio"], datetime.datetime.now())
            fim = _para_datetime(
                linha["fim"],
                inicio + datetime.timedelta(minutes=float(linha["duracao_min"] or 0)),
            )
            try:
                categoria = UserType(linha["user_type"])
            except ValueError:
                categoria = UserType.STANDARD

            potencia = float(linha["potencia_kw"] or 0.0)
            sessoes.append(ChargingSession(
                numero=indice,
                session_id=linha["session_id"],
                charger_id=linha["charger_id"],
                station_id=linha["station_id"],
                vehicle_id=linha["vehicle_id"],
                user_name=linha["user_name"],
                user_type=categoria,
                owner=linha["usuario"],
                requested_power_kw=potencia,
                allocated_power_kw=potencia,
                start_time=inicio,
                end_time=fim,
                energy_kwh=float(linha["energia_kwh"] or 0.0),
                tariff_kwh=float(linha["tarifa_kwh"] or 0.0),
                total_cost_brl=float(linha["custo_brl"] or 0.0),
                status=SessionStatus.FINISHED,
            ))
        except (TypeError, ValueError, IndexError, KeyError) as erro:
            logger.warning("Registro histórico ignorado (%s): %s",
                           erro, linha["session_id"] if linha else "?")

    return sessoes
