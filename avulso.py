# =============================================================================
#  ChargeGrid Intelligence — Recarga sem cadastro (QR do totem)
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Regras da recarga sem cadastro, liberada pelo QR Code do totem.

O motorista que não quer criar conta aponta a câmera para o QR do conector,
informa a placa, autoriza uma caução e carrega. No fim, a caução abate o
consumo e a diferença é estornada.

É o mesmo mecanismo que as redes públicas usam: a garantia do pagamento é o
valor pré-autorizado, não a identidade de quem carrega. Por isso aqui não há
senha, cadastro nem liberação manual por alguém do posto — qualquer um desses
passos devolveria o atrito que a modalidade existe para remover.

A sessão avulsa não tem carteira NexusCoin e não gera cashback: os dois
dependem de uma conta para creditar.

Como a sessão é identificada
----------------------------
Não há usuário para carimbar no campo `owner` da sessão, então ele guarda um
token aleatório com o prefixo `avulso:`. O token é o que o motorista leva na
URL para voltar e acompanhar ou encerrar a recarga — funciona como um bilhete:
quem tem o link tem a sessão, e nada além dessa sessão.
"""

from __future__ import annotations

import secrets

PREFIXO: str = "avulso:"

# Caução pré-autorizada na liberação do conector.
#
# O valor precisa cobrir uma recarga inteira, senão o motorista estoura a
# caução no meio e a modalidade perde a graça: na tarifa de pico (R$ 1,80/kWh)
# R$ 50 compram cerca de 28 kWh, aproximadamente 2h30 a 11 kW. As redes
# públicas brasileiras trabalham com valores bem mais altos — na faixa dos
# R$ 200 — mas aqui o número também precisa caber numa tela de demonstração.
CAUCAO_BRL: float = 50.00


def novo_dono() -> str:
    """Gera o identificador de uma sessão avulsa: `avulso:<token>`."""
    return f"{PREFIXO}{secrets.token_hex(6)}"


def eh_avulso(dono: str) -> bool:
    """Indica se o campo `owner` de uma sessão pertence a uma recarga avulsa."""
    return bool(dono) and dono.startswith(PREFIXO)


def dono_do_token(token: str) -> str:
    """Reconstrói o `owner` a partir do token que veio na URL."""
    return f"{PREFIXO}{token}"


def estorno(sinal_abatido_brl: float) -> float:
    """
    Quanto da caução volta para o motorista.

    `sinal_abatido_brl` é o que a cobrança conseguiu abater — no máximo o
    subtotal da recarga. O que sobrou da caução é devolvido.

    Consumo de R$ 18,40 numa caução de R$ 50 devolve R$ 31,60; consumo de
    R$ 72,00 abate os R$ 50 inteiros, devolve zero e deixa R$ 22,00 a pagar.
    """
    return round(max(CAUCAO_BRL - max(sinal_abatido_brl, 0.0), 0.0), 2)
