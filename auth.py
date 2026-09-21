# =============================================================================
#  ChargeGrid Intelligence — Contas e Autenticação (mockup)
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Autenticação simplificada para demonstração acadêmica.

Quatro contas fixas com uma senha única. Não há cadastro, recuperação de senha
nem hash — e isso é deliberado: o objetivo do Sprint 3 é demonstrar o fluxo de
sessão, carteira e pagamento, não construir um provedor de identidade.

Em produção esta camada seria substituída por um provedor real (OAuth2/OIDC)
sem tocar no resto do sistema: os demais módulos só consultam a chave do
usuário guardada no cookie de sessão do Flask.

Os saldos iniciais são escolhidos para que cada conta demonstre um caminho
diferente do produto:
    amanda (100 NC, assinante)    → caminho feliz: paga com NexusCoin e recebe cashback
    allan  (5 NC, corporativo)    → saldo insuficiente: bloqueio, recarga e retomada
    jose   (0 NC, padrão)         → carteira zerada: estado vazio do extrato,
                                    tarifa cheia sem desconto e pagamento por
                                    Pix ou cartão sem passar pela carteira
    mylon  (10.000 NC, operador)  → painel, throttle e Modbus, sem esbarrar em saldo
"""

from __future__ import annotations

from typing import Optional, TypedDict

from models import UserType


class Conta(TypedDict):
    """Perfil de uma conta pré-cadastrada."""

    nome:          str
    tipo:          UserType
    staff:         bool
    saldo_inicial: float
    cartao_final:  str
    cartao_bandeira: str


SENHA_PADRAO: str = "1234"

CONTAS: dict[str, Conta] = {
    "amanda": {
        "nome": "Amanda Ribeiro",
        "tipo": UserType.SUBSCRIBER,
        "staff": False,
        "saldo_inicial": 100.0,
        "cartao_final": "1234",
        "cartao_bandeira": "Mastercard",
    },
    "allan": {
        "nome": "Allan Souza",
        "tipo": UserType.CORPORATE,
        "staff": False,
        "saldo_inicial": 5.0,
        "cartao_final": "1234",
        "cartao_bandeira": "Mastercard",
    },
    "jose": {
        "nome": "José Fino",
        "tipo": UserType.STANDARD,
        "staff": False,
        "saldo_inicial": 0.0,
        "cartao_final": "1234",
        "cartao_bandeira": "Mastercard",
    },
    "mylon": {
        "nome": "Mylon Freixo",
        "tipo": UserType.STANDARD,
        "staff": True,
        "saldo_inicial": 10000.0,
        "cartao_final": "1234",
        "cartao_bandeira": "Mastercard",
    },
}


def autenticar(login: str, senha: str) -> Optional[str]:
    """
    Valida as credenciais informadas.

    Args:
        login : nome de usuário (espaços e caixa são ignorados)
        senha : senha em texto plano

    Returns:
        A chave da conta em CONTAS quando as credenciais conferem, senão None.
    """
    chave = (login or "").strip().lower()
    if chave in CONTAS and (senha or "") == SENHA_PADRAO:
        return chave
    return None


def conta(chave: str) -> Optional[Conta]:
    """Retorna o perfil da conta, ou None se a chave não existir."""
    return CONTAS.get(chave)


def nome_curto(chave: str) -> str:
    """Primeiro nome do titular, para saudações. Vazio se a conta não existir."""
    perfil = CONTAS.get(chave)
    return perfil["nome"].split()[0] if perfil else ""


def iniciais(chave: str) -> str:
    """
    Iniciais do titular para o avatar (ex.: 'Amanda Ribeiro' → 'AR').

    Usadas apenas como fallback textual; o avatar padrão é um ícone genérico.
    """
    perfil = CONTAS.get(chave)
    if not perfil:
        return "?"
    partes = perfil["nome"].split()
    return (partes[0][0] + (partes[-1][0] if len(partes) > 1 else "")).upper()


if __name__ == "__main__":
    assert autenticar("Amanda", "1234") == "amanda", "login deve ignorar caixa"
    assert autenticar("amanda", "errada") is None, "senha errada deve falhar"
    assert autenticar("ninguem", "1234") is None, "conta inexistente deve falhar"
    assert conta("mylon")["staff"] is True, "mylon deve ser staff"
    assert conta("amanda")["staff"] is False, "amanda não é staff"
    assert iniciais("amanda") == "AR"
    assert conta("jose")["saldo_inicial"] == 0.0, "josé começa sem créditos"
    assert conta("jose")["tipo"] is UserType.STANDARD, "josé é usuário padrão"
    assert iniciais("jose") == "JF"
    assert sum(1 for c in CONTAS.values() if c["staff"]) == 1, "só um operador"
    print(f"auth.py OK — {len(CONTAS)} contas")
