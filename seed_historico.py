#!/usr/bin/env python3
# =============================================================================
#  ChargeGrid Intelligence — Gerador de Histórico Sintético
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Popula a tabela `sessoes` com um histórico plausível de recargas.

Motivo: as análises estatísticas (probabilidade e regressão linear) e o
sistema de gerenciamento em linha de comando precisam de algumas centenas de
registros. Clicar 300 vezes na interface não é uma opção, e inventar números
à mão produziria uma distribuição sem relação com as regras do sistema.

Cada sessão é gerada **pelas mesmas regras do produto**:
    - horário sorteado de uma distribuição com pico às 19h (chegada do
      trabalho) e um segundo pico menor no almoço
    - tarifa calculada pela PricingEngine real (pico ×1,5, demanda ×1,3,
      desconto por categoria) — não é um número arbitrário
    - potência entre o piso trifásico (4,2 kW) e o nominal do GW11K-HCA-20
      (11 kW), com sessões em throttle recebendo menos
    - custo = energia × tarifa, respeitando a taxa mínima de R$ 2,00

Isso importa para a regressão: como energia = potência × tempo, o coeficiente
angular de duração → energia reproduz a potência média real da instalação, e
a dispersão em torno da reta é exatamente o efeito do throttling.

Uso:
    python seed_historico.py            # 300 sessões
    python seed_historico.py 500        # 500 sessões
    python seed_historico.py 300 --limpar
"""

from __future__ import annotations

import argparse
import datetime
import random
import sys
import uuid

import db
from models import UserType
from pricing_engine import (DESCONTO_ASSINANTE, DESCONTO_CORPORATE,
                            LIMIAR_DEMANDA, MULTIPLICADOR_DEMANDA,
                            MULTIPLICADOR_PICO, TARIFA_BASE_KWH,
                            TAXA_MINIMA_SESSAO)

POSTOS = {"P1": "Paulista", "P2": "Faria Lima", "P3": "Berrini"}
CONECTORES = [f"C{i}" for i in range(1, 6)]

# Proporção de categorias observada no segmento comercial de varejo.
CATEGORIAS = (
    [UserType.STANDARD]   * 60 +
    [UserType.CORPORATE]  * 25 +
    [UserType.SUBSCRIBER] * 15
)

# Peso relativo de início de sessão por hora do dia. Dois picos: almoço
# (12h–14h) e chegada do trabalho (18h–20h), com madrugada quase vazia.
PESO_HORA = [
    1, 1, 1, 1, 1, 2,      # 00–05
    4, 8, 14, 16, 15, 18,  # 06–11
    26, 24, 20, 18, 20, 28,  # 12–17
    38, 42, 34, 22, 12, 5,   # 18–23
]

METODOS = ["NEXUSCOIN"] * 55 + ["PIX"] * 30 + ["CARTAO"] * 15

CASHBACK = 0.10
SINAL_RESERVA = 10.0
PROB_RESERVA = 0.18       # fração de sessões precedidas de reserva
PROB_THROTTLE = 0.30      # fração que sofreu redistribuição de potência


def _placa() -> str:
    """Placa no padrão Mercosul (ABC1D23)."""
    letras = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return ("".join(random.choice(letras) for _ in range(3))
            + str(random.randint(0, 9))
            + random.choice(letras)
            + f"{random.randint(0, 99):02d}")


def _tarifa(hora: int, categoria: UserType, ocupacao: float) -> float:
    """Reproduz a fórmula da PricingEngine sem instanciar o app inteiro."""
    tarifa = TARIFA_BASE_KWH
    if 18 <= hora <= 22:
        tarifa = round(tarifa * MULTIPLICADOR_PICO, 4)
    if ocupacao >= LIMIAR_DEMANDA:
        tarifa = round(tarifa * MULTIPLICADOR_DEMANDA, 4)
    desconto = {UserType.SUBSCRIBER: DESCONTO_ASSINANTE,
                UserType.CORPORATE:  DESCONTO_CORPORATE}.get(categoria, 0.0)
    if desconto:
        tarifa = round(tarifa * (1 - desconto), 4)
    return tarifa


def gerar(n: int, dias: int = 45) -> int:
    """
    Gera `n` sessões distribuídas nos últimos `dias` dias.

    Returns:
        Quantidade efetivamente inserida.
    """
    db.init()
    agora = datetime.datetime.now()
    inseridas = 0

    for _ in range(n):
        hora = random.choices(range(24), weights=PESO_HORA, k=1)[0]
        inicio = (agora
                  - datetime.timedelta(days=random.randint(0, dias - 1))
                  ).replace(hour=hora,
                            minute=random.choice([0, 5, 10, 15, 20, 25, 30,
                                                  35, 40, 45, 50, 55]),
                            second=0, microsecond=0)

        # Ocupação é maior nos horários de pico — é o que dispara o eixo de
        # demanda da tarifa.
        ocupacao = min(0.95, max(0.05, PESO_HORA[hora] / 45 + random.uniform(-0.12, 0.12)))

        em_throttle = random.random() < PROB_THROTTLE
        potencia = (round(random.uniform(4.2, 9.5), 1) if em_throttle
                    else round(random.uniform(9.6, 11.0), 1))

        duracao_min = round(random.triangular(12, 240, 48), 1)
        energia = round(potencia * duracao_min / 60.0, 3)

        categoria = random.choice(CATEGORIAS)
        tarifa = _tarifa(hora, categoria, ocupacao)
        custo = max(round(energia * tarifa, 2), TAXA_MINIMA_SESSAO)

        teve_reserva = random.random() < PROB_RESERVA
        sinal = round(min(SINAL_RESERVA, custo), 2) if teve_reserva else 0.0
        pago = round(custo - sinal, 2)

        metodo = random.choice(METODOS)
        cashback = round(pago * CASHBACK, 2) if metodo == "NEXUSCOIN" else 0.0

        posto = random.choice(list(POSTOS))
        fim = inicio + datetime.timedelta(minutes=duracao_min)

        db.execute(
            "INSERT OR IGNORE INTO sessoes ("
            " session_id, usuario, charger_id, station_id, vehicle_id, user_name,"
            " user_type, inicio, fim, hora_inicio, duracao_min, potencia_kw,"
            " energia_kwh, tarifa_kwh, custo_brl, metodo_pagto, sinal_abatido,"
            " cashback_nc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"CGI-{uuid.uuid4().hex[:8].upper()}",
                "sintetico",
                f"{posto}-{random.choice(CONECTORES)}",
                posto,
                _placa(),
                "Histórico sintético",
                categoria.value,
                inicio.strftime("%Y-%m-%d %H:%M:%S"),
                fim.strftime("%Y-%m-%d %H:%M:%S"),
                hora,
                duracao_min,
                potencia,
                energia,
                tarifa,
                custo,
                metodo,
                sinal,
                cashback,
            ),
        )
        inseridas += 1

    return inseridas


def resumo() -> None:
    """Imprime as estatísticas do que existe hoje na tabela."""
    linha = db.query_one(
        "SELECT COUNT(*) AS n, "
        "       COALESCE(SUM(energia_kwh), 0) AS energia, "
        "       COALESCE(SUM(custo_brl), 0)   AS receita, "
        "       COALESCE(AVG(custo_brl), 0)   AS ticket, "
        "       COALESCE(MAX(energia_kwh), 0) AS maior, "
        "       COALESCE(MIN(energia_kwh), 0) AS menor "
        "FROM sessoes"
    )
    print()
    print("========= HISTÓRICO ARMAZENADO =========")
    print(f"  Sessões registradas : {linha['n']}")
    print(f"  Energia fornecida   : {linha['energia']:.2f} kWh")
    print(f"  Faturamento         : R$ {linha['receita']:.2f}")
    print(f"  Ticket médio        : R$ {linha['ticket']:.2f}")
    print(f"  Maior consumo       : {linha['maior']:.2f} kWh")
    print(f"  Menor consumo       : {linha['menor']:.2f} kWh")
    print("========================================")
    print()
    print("  Exporte em /admin/export.csv (perfil operador) para alimentar")
    print("  as análises estatísticas e o gerenciador em linha de comando.")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera histórico sintético de sessões para o ChargeGrid."
    )
    parser.add_argument("quantidade", nargs="?", type=int, default=300,
                        help="número de sessões a gerar (padrão: 300)")
    parser.add_argument("--limpar", action="store_true",
                        help="apaga o histórico existente antes de gerar")
    parser.add_argument("--semente", type=int, default=None,
                        help="semente do gerador aleatório (reprodutibilidade)")
    args = parser.parse_args()

    if args.quantidade < 1 or args.quantidade > 100_000:
        print("Quantidade deve estar entre 1 e 100.000.", file=sys.stderr)
        return 1

    if args.semente is not None:
        random.seed(args.semente)

    db.init()
    if args.limpar:
        db.execute("DELETE FROM sessoes")
        print("Histórico anterior removido.")

    n = gerar(args.quantidade)
    print(f"{n} sessões geradas.")
    resumo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
