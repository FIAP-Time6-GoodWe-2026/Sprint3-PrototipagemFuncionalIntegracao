#!/usr/bin/env python3
# =============================================================================
#  ChargeGrid Intelligence — Menu de Terminal (Estruturas de Dados)
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Menu de terminal do ChargeGrid Intelligence — segundo ponto de entrada do
mesmo sistema que a interface web serve.

As sessões são objetos `models.ChargingSession`, ficam na lista do
`SessionManager`, o histórico vem de `db.carregar_sessoes()` e a busca e a
ordenação são as de `algoritmos.py` — as mesmas que o `get_session` e o
`rebalance` usam.

Este módulo **não importa Flask**. Roda com Python puro, sem nenhuma
dependência instalada: `import` de `algoritmos`, `models`, `session_manager`,
`db` (sqlite3 é stdlib) e nada mais.

Uso:
    python menu.py

O menu é somente leitura em relação ao banco: a sessão cadastrada na opção 1
vive na memória daquela execução. Isso é deliberado — quem escreve no
histórico é a camada web, depois do pagamento confirmado.
"""

from __future__ import annotations

import datetime
import sys
import time
from typing import List, Optional

import algoritmos
import db
from models import ChargingSession, SessionStatus, UserType
from session_manager import SessionManager

# ---------------------------------------------------------------------------
# Apresentação
# ---------------------------------------------------------------------------

LARGURA = 79

# Console do Windows nem sempre está em UTF-8 (o padrão em pt-BR é cp850).
# Trocar apenas a política de erro mantém a página de código do console e
# impede que um caractere não representável derrube o programa.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, OSError):   # pragma: no cover - varia por plataforma
    pass


def titulo(texto: str) -> None:
    """Imprime um cabeçalho de seção delimitado."""
    print()
    print("=" * LARGURA)
    print(texto.center(LARGURA))
    print("=" * LARGURA)


def aviso(texto: str) -> None:
    """Imprime uma mensagem de erro ou alerta em linha destacada."""
    print(f"  [!] {texto}")


def exibir_menu() -> None:
    """Mostra as opções disponíveis."""
    print()
    print("=" * 37)
    print("        ESTAÇÃO DE RECARGA".ljust(37))
    print("      ChargeGrid Intelligence".ljust(37))
    print("=" * 37)
    print()
    print("1 - Nova sessão de recarga")
    print("2 - Listar sessões")
    print("3 - Buscar sessão")
    print("4 - Ordenar sessões")
    print("5 - Estatísticas")
    print("6 - Comparar algoritmos (Big-O na prática)")
    print("7 - Encerrar")
    print()


# ---------------------------------------------------------------------------
# Leitura validada de entrada
# ---------------------------------------------------------------------------

TENTATIVAS_MAXIMAS = 3


def ler_texto(rotulo: str, obrigatorio: bool = True,
              padrao: str = "") -> Optional[str]:
    """
    Lê um texto do teclado.

    Args:
        rotulo      : pergunta exibida
        obrigatorio : quando True, recusa string vazia
        padrao      : valor adotado quando o usuário só aperta Enter

    Returns:
        O texto digitado, o padrão, ou None quando o usuário cancela
        (Enter vazio sem padrão definido em campo obrigatório).
    """
    sufixo = f" [{padrao}]" if padrao else ""
    for _ in range(TENTATIVAS_MAXIMAS):
        valor = input(f"  {rotulo}{sufixo}: ").strip()
        if valor:
            return valor
        if padrao:
            return padrao
        if not obrigatorio:
            return ""
        aviso("Campo obrigatório. Enter vazio de novo cancela.")
        obrigatorio = False   # a segunda tentativa vazia cancela
    return None


def ler_numero(rotulo: str, minimo: Optional[float] = None,
               maximo: Optional[float] = None, inteiro: bool = False,
               padrao: Optional[float] = None) -> Optional[float]:
    """
    Lê um número do teclado, validando tipo e faixa, com até 3 tentativas.

    Cobre as três formas de o operador errar aqui:
        - texto onde se espera número  → `ValueError` capturado
        - valor fora da faixa          → recusado com a faixa na mensagem
        - desistência                  → Enter vazio cancela

    Args:
        rotulo  : pergunta exibida
        minimo  : menor valor aceito (inclusive), None = sem piso
        maximo  : maior valor aceito (inclusive), None = sem teto
        inteiro : quando True, devolve int e recusa fracionário
        padrao  : valor adotado quando o usuário só aperta Enter

    Returns:
        O número lido, ou None quando o usuário cancela ou esgota as
        3 tentativas. Nunca levanta exceção.
    """
    sufixo = f" [{padrao:g}]" if padrao is not None else ""
    faixa = ""
    if minimo is not None and maximo is not None:
        faixa = f" (entre {minimo:g} e {maximo:g})"
    elif minimo is not None:
        faixa = f" (mínimo {minimo:g})"
    elif maximo is not None:
        faixa = f" (máximo {maximo:g})"

    for tentativa in range(1, TENTATIVAS_MAXIMAS + 1):
        bruto = input(f"  {rotulo}{faixa}{sufixo}: ").strip()

        if not bruto:
            if padrao is not None:
                return int(padrao) if inteiro else float(padrao)
            print("  Cancelado.")
            return None

        try:
            valor = int(bruto) if inteiro else float(bruto.replace(",", "."))
        except ValueError:
            restam = TENTATIVAS_MAXIMAS - tentativa
            tipo = "número inteiro" if inteiro else "número"
            aviso(f"'{bruto}' não é um {tipo}. "
                  + (f"Restam {restam} tentativa(s)." if restam
                     else "Tentativas esgotadas."))
            continue

        if minimo is not None and valor < minimo:
            aviso(f"Valor mínimo é {minimo:g}.")
            continue
        if maximo is not None and valor > maximo:
            aviso(f"Valor máximo é {maximo:g}.")
            continue

        return valor

    return None


def ler_opcao(rotulo: str, validas: dict) -> Optional[str]:
    """
    Lê uma escolha entre alternativas pré-definidas.

    Args:
        rotulo : pergunta exibida
        validas: {tecla → descrição} das alternativas aceitas

    Returns:
        A tecla escolhida, ou None se o usuário cancelar ou errar 3 vezes.
    """
    for chave, descricao in validas.items():
        print(f"    {chave} - {descricao}")
    for _ in range(TENTATIVAS_MAXIMAS):
        escolha = input(f"  {rotulo}: ").strip()
        if not escolha:
            print("  Cancelado.")
            return None
        if escolha in validas:
            return escolha
        aviso(f"Opção '{escolha}' inválida. Escolha uma das listadas.")
    return None


# ---------------------------------------------------------------------------
# Formatação de sessão
# ---------------------------------------------------------------------------

def detalhar(sessao: ChargingSession) -> None:
    """
    Imprime TODOS os dados da sessão.

    Achar a sessão e só dizer "encontrada" não serve para nada: quem
    procura quer ver o registro.
    """
    print()
    print(f"  {'ID (número)':<22}: {sessao.numero}")
    print(f"  {'Identificador único':<22}: {sessao.session_id}")
    print(f"  {'Conector / Posto':<22}: {sessao.charger_id} / {sessao.station_id}")
    print(f"  {'Veículo':<22}: {sessao.vehicle_id}")
    print(f"  {'Usuário':<22}: {sessao.user_name} ({sessao.user_type.value})")
    print(f"  {'Status':<22}: {sessao.status.value}")
    print(f"  {'Início':<22}: {sessao.start_time.strftime('%d/%m/%Y %H:%M:%S')}")
    fim = (sessao.end_time.strftime('%d/%m/%Y %H:%M:%S')
           if sessao.end_time else "em andamento")
    print(f"  {'Fim':<22}: {fim}")
    print(f"  {'Tempo de recarga':<22}: {sessao.duration_minutes:.1f} min")
    print(f"  {'Potência solicitada':<22}: {sessao.requested_power_kw:.1f} kW")
    print(f"  {'Potência alocada':<22}: {sessao.allocated_power_kw:.1f} kW")
    print(f"  {'Energia consumida':<22}: {sessao.energy_kwh:.3f} kWh")
    print(f"  {'Tarifa aplicada':<22}: R$ {sessao.tariff_kwh:.4f}/kWh")
    print(f"  {'Custo total':<22}: R$ {sessao.total_cost_brl:.2f}")
    print()


def imprimir_tabela(colecao: List[ChargingSession], limite: int = 20) -> None:
    """Imprime a coleção como tabela, truncando para não inundar o terminal."""
    if not colecao:
        print("  Nenhuma sessão armazenada.")
        return

    cabecalho = (f"  {'ID':>5}  {'Conector':<9} {'Usuário':<22} "
                 f"{'Energia':>10} {'Tempo':>9} {'Custo':>10}")
    print(cabecalho)
    print("  " + "-" * (len(cabecalho) - 2))
    for sessao in colecao[:limite]:
        nome = sessao.user_name[:22]
        print(f"  {sessao.numero:>5}  {sessao.charger_id:<9} {nome:<22} "
              f"{sessao.energy_kwh:>7.2f}kWh {sessao.duration_minutes:>6.1f}min "
              f"R$ {sessao.total_cost_brl:>7.2f}")
    if len(colecao) > limite:
        print(f"  ... e mais {len(colecao) - limite} sessão(ões) "
              f"(mostrando as {limite} primeiras da ordem atual)")


# ---------------------------------------------------------------------------
# Opção 1 — Nova sessão de recarga
# ---------------------------------------------------------------------------

CATEGORIAS = {
    "1": "Padrão (P) — tarifa base",
    "2": "Assinante (A) — 15% de desconto",
    "3": "Corporativo (C) — 10% de desconto",
}
CATEGORIA_POR_TECLA = {
    "1": UserType.STANDARD,
    "2": UserType.SUBSCRIBER,
    "3": UserType.CORPORATE,
}


def cadastrar_sessao(gerenciador: SessionManager) -> None:
    """
    Registra uma sessão de recarga encerrada na coleção.

    Usa `SessionManager.registrar_historico`, não `create_session`: estamos
    anexando um fato passado, e não iniciando uma recarga agora — não faz
    sentido reservar um conector físico para isso (ver a docstring do método).

    O tempo digitado é convertido em `end_time`, porque no domínio do
    ChargeGrid a duração é derivada dos carimbos de tempo
    (`ChargingSession.duration_minutes`) em vez de ser um campo solto: é o
    mesmo intervalo que o motor de energia usa para calcular kWh, então os
    dois nunca podem divergir.

    Enter vazio em qualquer campo cancela o cadastro.
    """
    titulo("NOVA SESSÃO DE RECARGA")
    colecao = gerenciador.sessions
    sugerido = algoritmos.proximo_numero(colecao)
    print(f"  Enter vazio cancela. ID sugerido: {sugerido}")
    print()

    numero = ler_numero("ID da sessão", minimo=1, inteiro=True, padrao=sugerido)
    if numero is None:
        return
    numero = int(numero)

    # Validação de ID duplicado — via busca sequencial
    if algoritmos.numero_existe(colecao, numero):
        aviso(f"Já existe sessão com ID {numero}. Cadastro cancelado.")
        return

    veiculo = ler_texto("Placa do veículo", padrao="ABC1D23")
    if veiculo is None:
        return

    conector = ler_texto("Conector (ex.: P1-C3)", padrao="P1-C1")
    if conector is None:
        return
    if "-" not in conector:
        aviso("Conector deve ter o formato POSTO-CONECTOR (ex.: P1-C3).")
        return
    posto = conector.split("-")[0]

    motorista = ler_texto("Nome do motorista", padrao="Operador")
    if motorista is None:
        return

    print("  Categoria tarifária:")
    tecla = ler_opcao("Categoria", CATEGORIAS)
    if tecla is None:
        return
    categoria = CATEGORIA_POR_TECLA[tecla]

    energia = ler_numero("Energia consumida (kWh)", minimo=0.0, maximo=1000.0)
    if energia is None:
        return

    tempo = ler_numero("Tempo de recarga (min)", minimo=0.1, maximo=1440.0)
    if tempo is None:
        return

    custo = ler_numero("Custo total (R$)", minimo=0.0, maximo=100000.0)
    if custo is None:
        return

    inicio = datetime.datetime.now()
    sessao = ChargingSession(
        numero=numero,
        charger_id=conector,
        station_id=posto,
        vehicle_id=veiculo,
        user_name=motorista,
        user_type=categoria,
        requested_power_kw=round(energia / (tempo / 60.0), 2) if tempo else 0.0,
        allocated_power_kw=round(energia / (tempo / 60.0), 2) if tempo else 0.0,
        start_time=inicio,
        end_time=inicio + datetime.timedelta(minutes=tempo),
        energy_kwh=energia,
        tariff_kwh=round(custo / energia, 4) if energia else 0.0,
        total_cost_brl=custo,
        status=SessionStatus.FINISHED,
    )

    try:
        gerenciador.registrar_historico(sessao)
    except ValueError as erro:
        aviso(str(erro))
        return

    print()
    print(f"  Sessão registrada. A coleção passou a ter "
          f"{len(gerenciador.sessions)} sessão(ões).")
    detalhar(sessao)


# ---------------------------------------------------------------------------
# Opção 2 — Listar sessões
# ---------------------------------------------------------------------------

def listar_sessoes(gerenciador: SessionManager) -> None:
    """
    Lista a coleção na ordem atual de armazenamento.

    A contagem sai de `len()` sobre a lista.
    """
    colecao = gerenciador.sessions
    titulo(f"SESSÕES ARMAZENADAS: {len(colecao)}")
    imprimir_tabela(colecao)


# ---------------------------------------------------------------------------
# Opção 3 — Buscar sessão
# ---------------------------------------------------------------------------

ALGORITMOS_BUSCA = {
    "1": "Busca sequencial  — O(n), não exige lista ordenada",
    "2": "Busca binária     — O(log n), exige lista ordenada por ID",
}


def buscar_sessao(gerenciador: SessionManager) -> None:
    """
    Procura uma sessão por ID, com o algoritmo escolhido pelo usuário.

    A busca binária tem pré-condição: a lista precisa estar ordenada pela
    mesma chave. Em vez de esconder isso, o menu ordena com
    `algoritmos.insertion_sort`, informa na tela quantas comparações a
    ordenação custou e só então busca — deixando visível que o custo real da
    binária sobre lista desordenada é ordenação + busca.
    """
    colecao = gerenciador.sessions
    titulo("BUSCAR SESSÃO")
    if not colecao:
        print("  Nenhuma sessão armazenada. Use a opção 1 para cadastrar.")
        return

    print("  Algoritmo de busca:")
    tecla = ler_opcao("Algoritmo", ALGORITMOS_BUSCA)
    if tecla is None:
        return

    alvo = ler_numero("ID procurado", minimo=1, inteiro=True)
    if alvo is None:
        return
    alvo = int(alvo)

    print()
    if tecla == "1":
        inicio = time.perf_counter()
        indice, comparacoes = algoritmos.busca_sequencial(colecao, alvo)
        decorrido = (time.perf_counter() - inicio) * 1000
        print(f"  Busca sequencial em {len(colecao)} sessões: "
              f"{comparacoes} comparação(ões), {decorrido:.3f} ms")
    else:
        print("  Pré-condição da busca binária: lista ordenada por ID.")
        inicio = time.perf_counter()
        comp_ordenacao = algoritmos.insertion_sort(colecao,
                                                   algoritmos.CRITERIOS["1"][1])
        t_ordenacao = (time.perf_counter() - inicio) * 1000
        print(f"  Ordenando com insertion sort: {comp_ordenacao} "
              f"comparação(ões), {t_ordenacao:.3f} ms")

        inicio = time.perf_counter()
        indice, comparacoes = algoritmos.busca_binaria(colecao, alvo)
        t_busca = (time.perf_counter() - inicio) * 1000
        print(f"  Busca binária em {len(colecao)} sessões: "
              f"{comparacoes} comparação(ões), {t_busca:.3f} ms")
        print(f"  Custo total (ordenar + buscar): "
              f"{comp_ordenacao + comparacoes} comparação(ões)")

    if indice < 0:
        aviso(f"Nenhuma sessão com ID {alvo}.")
        return

    print(f"  Encontrada na posição {indice} da lista.")
    detalhar(colecao[indice])


# ---------------------------------------------------------------------------
# Opção 4 — Ordenar sessões
# ---------------------------------------------------------------------------

ALGORITMOS_ORDENACAO = {
    "1": "Bubble sort     — O(n²) sempre, exatamente n(n-1)/2 comparações",
    "2": "Insertion sort  — O(n²) pior caso, O(n) se já estiver ordenada",
}


def ordenar_sessoes(gerenciador: SessionManager) -> None:
    """
    Ordena a coleção pelo critério e algoritmo escolhidos pelo usuário.

    A ordenação é **in-place** sobre a lista viva do `SessionManager`: a nova
    ordem persiste para as demais opções do menu.
    """
    colecao = gerenciador.sessions
    titulo("ORDENAR SESSÕES")
    if len(colecao) < 2:
        print(f"  A coleção tem {len(colecao)} sessão(ões) — nada a ordenar.")
        return

    print("  Critério de ordenação:")
    criterios = {chave: rotulo for chave, (rotulo, _) in
                 algoritmos.CRITERIOS.items()}
    tecla_criterio = ler_opcao("Criterio", criterios)
    if tecla_criterio is None:
        return
    rotulo, chave = algoritmos.CRITERIOS[tecla_criterio]

    print("  Algoritmo de ordenação:")
    tecla_algoritmo = ler_opcao("Algoritmo", ALGORITMOS_ORDENACAO)
    if tecla_algoritmo is None:
        return

    n = len(colecao)
    inicio = time.perf_counter()
    if tecla_algoritmo == "1":
        comparacoes = algoritmos.bubble_sort(colecao, chave)
        nome = "Bubble sort"
        esperado = f"  Fórmula n(n-1)/2 = {n * (n - 1) // 2} comparações"
    else:
        comparacoes = algoritmos.insertion_sort(colecao, chave)
        nome = "Insertion sort"
        esperado = (f"  Faixa do insertion: melhor caso {n - 1}, "
                    f"pior caso {n * (n - 1) // 2}")
    decorrido = (time.perf_counter() - inicio) * 1000

    print()
    print(f"  {nome} por {rotulo}: n = {n}, {comparacoes} comparações, "
          f"{decorrido:.3f} ms")
    print(esperado)
    print()
    imprimir_tabela(colecao)


# ---------------------------------------------------------------------------
# Opção 5 — Estatísticas
# ---------------------------------------------------------------------------

def mostrar_estatisticas(gerenciador: SessionManager) -> None:
    """Exibe os indicadores agregados calculados por `algoritmos.estatisticas`."""
    colecao = gerenciador.sessions
    est = algoritmos.estatisticas(colecao)
    titulo("ESTATÍSTICAS DA OPERAÇÃO")
    print(f"  {'Total de sessões':<26}: {est['total_sessoes']}")
    print(f"  {'Energia total entregue':<26}: {est['energia_total_kwh']:.3f} kWh")
    print(f"  {'Faturamento total':<26}: R$ {est['faturamento_brl']:.2f}")
    print(f"  {'Ticket médio por sessão':<26}: R$ {est['ticket_medio_brl']:.2f}")
    print(f"  {'Maior consumo':<26}: {est['maior_consumo_kwh']:.3f} kWh "
          f"(sessão #{est['numero_maior_consumo']})")
    print(f"  {'Menor consumo':<26}: {est['menor_consumo_kwh']:.3f} kWh "
          f"(sessão #{est['numero_menor_consumo']})")
    print("  " + "-" * (LARGURA - 4))
    print(f"  {'Energia média por sessão':<26}: {est['energia_media_kwh']:.3f} kWh")
    print(f"  {'Tempo total de recarga':<26}: {est['tempo_total_min']:.1f} min")
    if not colecao:
        print()
        print("  Coleção vazia: todos os indicadores em zero, sem erro.")


# ---------------------------------------------------------------------------
# Opção 6 — Comparar algoritmos
# ---------------------------------------------------------------------------

def _amostras(n: int) -> List[int]:
    """Tamanhos de entrada usados na tabela de comparação, até n."""
    candidatos = [10, 25, 50, 100, 200, 400]
    escolhidos = [t for t in candidatos if t < n]
    escolhidos.append(n)
    return escolhidos


def comparar_algoritmos(gerenciador: SessionManager) -> None:
    """
    Mede busca e ordenação sobre a coleção real, em vários tamanhos de entrada.

    Relaciona tamanho da entrada, número de operações, classe de
    complexidade e tempo. Os números que esta opção imprime são os que
    aparecem no relatório.
    """
    colecao = gerenciador.sessions
    titulo("COMPARAÇÃO DE ALGORITMOS")
    if len(colecao) < 2:
        print(f"  A coleção tem {len(colecao)} sessão(ões) — "
              f"pouco para comparar. Carregue o histórico ou cadastre.")
        return

    por_id = algoritmos.CRITERIOS["1"][1]
    por_energia = algoritmos.CRITERIOS["2"][1]

    # --- BUSCA ---------------------------------------------------------
    print()
    print("  BUSCA — alvo ausente (pior caso para as duas)")
    print(f"  {'n':>6} {'sequencial O(n)':>17} {'binária O(log n)':>18} "
          f"{'redução':>9}")
    print("  " + "-" * 54)
    for n in _amostras(len(colecao)):
        amostra = list(colecao[:n])
        algoritmos.insertion_sort(amostra, por_id)   # pré-condição da binária
        ausente = algoritmos.proximo_numero(amostra) + 1
        _, c_seq = algoritmos.busca_sequencial(amostra, ausente)
        _, c_bin = algoritmos.busca_binaria(amostra, ausente)
        print(f"  {n:>6} {c_seq:>17} {c_bin:>18} "
              f"{(1 - c_bin / c_seq) * 100:>8.1f}%")

    # --- ORDENACAO -----------------------------------------------------
    print()
    print("  ORDENAÇÃO por energia — comparações realizadas")
    print(f"  {'n':>6} {'bubble O(n²)':>15} {'n(n-1)/2':>10} "
          f"{'insertion':>11} {'insertion já ord.':>19} {'n-1':>6}")
    print("  " + "-" * 72)
    for n in _amostras(len(colecao)):
        base = list(colecao[:n])

        alvo_bubble = list(base)
        c_bubble = algoritmos.bubble_sort(alvo_bubble, por_energia)

        alvo_insertion = list(base)
        c_insertion = algoritmos.insertion_sort(alvo_insertion, por_energia)

        # Já ordenada: melhor caso do insertion, uma comparação por elemento
        c_melhor = algoritmos.insertion_sort(alvo_insertion, por_energia)

        print(f"  {n:>6} {c_bubble:>15} {n * (n - 1) // 2:>10} "
              f"{c_insertion:>11} {c_melhor:>19} {n - 1:>6}")

    # --- TEMPO ---------------------------------------------------------
    n = len(colecao)
    base = list(colecao)
    tempos = {}
    for nome, funcao in (("bubble_sort", algoritmos.bubble_sort),
                         ("insertion_sort", algoritmos.insertion_sort)):
        alvo = list(base)
        inicio = time.perf_counter()
        funcao(alvo, por_energia)
        tempos[nome] = (time.perf_counter() - inicio) * 1000

    print()
    print(f"  TEMPO DE PAREDE com n = {n} (uma execução, ordem original)")
    for nome, ms in tempos.items():
        print(f"    {nome:<16}: {ms:8.3f} ms")

    print()
    print("  Leitura: dobrar n multiplica por ~2 o trabalho da busca")
    print("  sequencial, soma ~1 comparação na binária e multiplica por ~4 o")
    print("  das duas ordenações — assinatura de O(n), O(log n) e O(n²).")


# ---------------------------------------------------------------------------
# Carregamento inicial
# ---------------------------------------------------------------------------

def carregar_colecao() -> SessionManager:
    """
    Monta o `SessionManager` com o histórico do banco.

    Cada registro entra por `registrar_historico`, o único caminho correto
    para uma sessão já encerrada. Banco ausente ou vazio devolve um
    gerenciador sem sessões — o menu abre e a opção 1 preenche.
    """
    gerenciador = SessionManager()
    historico = db.carregar_sessoes()
    ignorados = 0
    for sessao in historico:
        try:
            gerenciador.registrar_historico(sessao)
        except ValueError:
            ignorados += 1   # ID repetido no banco: mantém o primeiro
    print(f"  Histórico carregado: {len(gerenciador.sessions)} sessão(ões)"
          + (f", {ignorados} ignorada(s) por ID repetido." if ignorados
             else "."))
    return gerenciador


# ---------------------------------------------------------------------------
# Laço principal
# ---------------------------------------------------------------------------

ACOES = {
    "1": cadastrar_sessao,
    "2": listar_sessoes,
    "3": buscar_sessao,
    "4": ordenar_sessoes,
    "5": mostrar_estatisticas,
    "6": comparar_algoritmos,
}


def main() -> int:
    """
    Laço `while True` do menu, encerrando apenas na opção 7.

    Três redes de segurança, todas exigidas pelo item 8:
        - opção inválida apenas avisa e reexibe o menu
        - `EOFError` / `KeyboardInterrupt` encerram sem traceback
        - `except Exception` impede que qualquer falha inesperada dentro de
          uma opção derrube o programa; o menu volta a ser exibido
    """
    titulo("CHARGEGRID INTELLIGENCE — GESTÃO DE SESSÕES DE RECARGA")
    print("  Sprint 3 | FIAP + GoodWe EV Challenge 2026")
    print("  Estruturas de Dados: lista de sessões, busca e ordenação manuais")
    print()

    try:
        gerenciador = carregar_colecao()
    except Exception as erro:                       # pragma: no cover
        aviso(f"Não foi possível carregar o histórico ({erro}). "
              f"Começando vazio.")
        gerenciador = SessionManager()

    while True:
        try:
            exibir_menu()
            escolha = input("Escolha: ").strip()

            if escolha == "7":
                print()
                print("  Encerrando. Até logo.")
                return 0

            acao = ACOES.get(escolha)
            if acao is None:
                aviso(f"Opção '{escolha}' inválida. Escolha de 1 a 7.")
                continue

            acao(gerenciador)

        except (EOFError, KeyboardInterrupt):
            print()
            print("  Encerrado pelo usuário.")
            return 0
        except Exception as erro:                   # pragma: no cover
            aviso(f"Falha inesperada nesta operação: "
                  f"{type(erro).__name__}: {erro}")
            aviso("O menu continua ativo — nenhuma sessão foi perdida.")


if __name__ == "__main__":
    sys.exit(main())
