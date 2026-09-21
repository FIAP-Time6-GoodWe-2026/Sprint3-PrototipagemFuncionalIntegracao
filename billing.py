# =============================================================================
#  ChargeGrid Intelligence — Composição da Cobrança
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Cálculo da cobrança de uma sessão encerrada.

Separa o *cálculo* (aqui) da *forma de pagamento* (rota), de modo que o valor
exibido na tela seja exatamente o valor cobrado, qualquer que seja o método
escolhido pelo usuário.

O subtotal não é recalculado: vem de `ChargingSession.total_cost_brl`, que o
SessionManager já apurou com a tarifa fixada no momento da conexão e com a
taxa mínima aplicada no encerramento. Recalcular aqui abriria espaço para
divergência entre o que o medidor registrou e o que a fatura cobra.
"""

from __future__ import annotations

from dataclasses import dataclass

import wallet
from models import ChargingSession

METODO_NEXUSCOIN = "NEXUSCOIN"
METODO_PIX       = "PIX"
METODO_CARTAO    = "CARTAO"
METODOS = (METODO_NEXUSCOIN, METODO_PIX, METODO_CARTAO)

ROTULO_METODO = {
    METODO_NEXUSCOIN: "NexusCoin",
    METODO_PIX:       "Pix",
    METODO_CARTAO:    "Cartão de crédito",
}


@dataclass
class Cobranca:
    """
    Composição financeira de uma sessão pronta para pagamento.

    Campos
    ------
    subtotal_brl   : energia × tarifa, com a taxa mínima já aplicada
    sinal_brl      : parcela do sinal de reserva efetivamente abatida
    total_brl      : o que o usuário paga agora
    cashback_nc    : quanto voltaria em NexusCoin se pagasse com NexusCoin
    energia_kwh    : energia medida na sessão
    tarifa_kwh     : tarifa fixada na conexão
    duracao_min    : duração da sessão
    """

    subtotal_brl: float
    sinal_brl:    float
    total_brl:    float
    cashback_nc:  float
    energia_kwh:  float
    tarifa_kwh:   float
    duracao_min:  float

    @property
    def gratuito(self) -> bool:
        """True quando o sinal cobriu toda a recarga e não há nada a pagar."""
        return self.total_brl <= 0.0


def calcular(sessao: ChargingSession, sinal_brl: float = 0.0) -> Cobranca:
    """
    Monta a cobrança de uma sessão já encerrada.

    O sinal da reserva abate o subtotal, mas o total nunca fica negativo: um
    sinal maior que o consumo zera a conta e o excedente **não** vira crédito
    na carteira. Se virasse, reservar e carregar por um minuto seria uma forma
    de mover dinheiro de graça — o excedente é a contrapartida por ter
    bloqueado o conector.

    Args:
        sessao    : sessão encerrada (usa total_cost_brl, energy_kwh, tariff_kwh)
        sinal_brl : sinal pago na reserva, se houve

    Returns:
        Cobranca com todos os componentes já arredondados a 2 casas.
    """
    subtotal = round(max(sessao.total_cost_brl, 0.0), 2)
    abatido  = round(min(max(sinal_brl, 0.0), subtotal), 2)
    total    = round(subtotal - abatido, 2)

    return Cobranca(
        subtotal_brl=subtotal,
        sinal_brl=abatido,
        total_brl=total,
        cashback_nc=round(total * wallet.CASHBACK_NEXUSCOIN, 2),
        energia_kwh=round(sessao.energy_kwh, 3),
        tarifa_kwh=round(sessao.tariff_kwh, 4),
        duracao_min=round(sessao.duration_minutes, 1),
    )


def rotulo(metodo: str) -> str:
    """Nome do método de pagamento em português, para recibo e extrato."""
    return ROTULO_METODO.get(metodo, metodo)


def normalizar_metodo(bruto: str) -> str:
    """
    Valida o método vindo do formulário.

    Raises:
        ValueError : método desconhecido.
    """
    metodo = (bruto or "").strip().upper()
    if metodo not in METODOS:
        raise ValueError(f"Método de pagamento inválido: '{bruto}'.")
    return metodo


if __name__ == "__main__":
    import datetime

    from models import SessionStatus, UserType

    def _sessao(custo: float) -> ChargingSession:
        s = ChargingSession(
            charger_id="P1-C1", station_id="P1", vehicle_id="ABC1D23",
            user_name="Teste", user_type=UserType.STANDARD,
            requested_power_kw=11.0,
        )
        s.total_cost_brl = custo
        s.energy_kwh = 5.0
        s.tariff_kwh = 1.20
        s.end_time = s.start_time + datetime.timedelta(minutes=30)
        s.status = SessionStatus.FINISHED
        return s

    c = calcular(_sessao(25.00), sinal_brl=10.00)
    assert c.total_brl == 15.00, c
    assert c.cashback_nc == 1.50, c

    # Sinal maior que o consumo: zera a conta, sem crédito residual
    c = calcular(_sessao(4.00), sinal_brl=10.00)
    assert c.total_brl == 0.0 and c.sinal_brl == 4.00, c
    assert c.gratuito and c.cashback_nc == 0.0, c

    # Sem reserva
    c = calcular(_sessao(7.30))
    assert (c.total_brl, c.sinal_brl) == (7.30, 0.0), c

    assert normalizar_metodo(" pix ") == "PIX"
    try:
        normalizar_metodo("boleto")
        raise AssertionError("método inválido deveria falhar")
    except ValueError:
        pass
    print("billing.py OK")
