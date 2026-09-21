# =============================================================================
#  ChargeGrid Intelligence — QR Code simulado (SVG)
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Gera um SVG com a aparência de um QR Code, para a simulação de pagamento Pix.

ATENÇÃO — não é um QR Code válido. Os módulos vêm de um hash SHA-256 do
payload, não da codificação Reed-Solomon do padrão ISO/IEC 18004. Nenhum
leitor decodifica esta figura, e ela nunca deve ser apresentada como um BR Code
real: a interface exibe o rótulo "QR Code simulado" ao lado.

O resultado é determinístico — o mesmo payload gera sempre a mesma figura —,
o que mantém as capturas de tela estáveis entre execuções.

Para um BR Code Pix de verdade, o caminho seria `pip install qrcode` e montar
o payload EMV com a chave do recebedor. Fora do escopo de um mockup acadêmico.
"""

from __future__ import annotations

import hashlib

MODULOS_PADRAO: int = 25
LADO_MARCADOR: int = 7


def _dentro_marcador(x: int, y: int, modulos: int) -> tuple[bool, bool]:
    """
    Diz se a célula pertence a um dos três olhos de posicionamento.

    Returns:
        (pertence_ao_marcador, deve_ser_preta)
    """
    cantos = ((0, 0), (modulos - LADO_MARCADOR, 0), (0, modulos - LADO_MARCADOR))
    for cx, cy in cantos:
        if cx <= x < cx + LADO_MARCADOR and cy <= y < cy + LADO_MARCADOR:
            anel  = x in (cx, cx + 6) or y in (cy, cy + 6)
            miolo = (cx + 2 <= x <= cx + 4) and (cy + 2 <= y <= cy + 4)
            return True, (anel or miolo)
    return False, False


def qr_simulado(payload: str, modulos: int = MODULOS_PADRAO) -> str:
    """
    Devolve o markup SVG de um QR Code simulado.

    O SVG usa `fill="currentColor"`, então acompanha a cor de texto do
    container — funciona nos dois temas sem CSS adicional.

    Args:
        payload : texto que semeia o padrão (ex.: "CGI|CGI-1A2B3C|6.54")
        modulos : dimensão da grade (25 = aparência de QR versão 2)

    Returns:
        String com o elemento <svg> completo.
    """
    if modulos < 21:
        raise ValueError("Um QR precisa de pelo menos 21 módulos de lado.")

    semente = hashlib.sha256(payload.encode("utf-8")).digest()
    necessarios = modulos * modulos
    bits = "".join(f"{b:08b}" for b in semente * (necessarios // 256 + 2))

    quadrados: list[str] = []
    for y in range(modulos):
        for x in range(modulos):
            no_marcador, preta = _dentro_marcador(x, y, modulos)
            if not no_marcador:
                # Zona de silêncio ao redor dos marcadores, como num QR real
                vizinho, _ = _dentro_marcador(x, y, modulos)
                preta = bits[y * modulos + x] == "1" and not vizinho
            if preta:
                quadrados.append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')

    return (
        f'<svg viewBox="0 0 {modulos} {modulos}" role="img" '
        f'aria-label="QR Code simulado para pagamento via Pix" '
        f'shape-rendering="crispEdges" fill="currentColor" '
        f'width="100%" height="100%">'
        f'{"".join(quadrados)}'
        f'</svg>'
    )


if __name__ == "__main__":
    a = qr_simulado("CGI|TESTE|10.00")
    b = qr_simulado("CGI|TESTE|10.00")
    c = qr_simulado("CGI|OUTRO|10.00")
    assert a == b, "o mesmo payload deve gerar sempre a mesma figura"
    assert a != c, "payloads diferentes devem gerar figuras diferentes"
    assert a.count("<rect") > 200, "densidade baixa demais para parecer um QR"
    assert 'aria-label' in a, "o SVG precisa de rótulo acessível"
    print(f"qr.py OK — {a.count('<rect')} módulos preenchidos")
