# =============================================================================
#  ChargeGrid Intelligence — Algoritmos de Busca, Ordenação e Estatística
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Algoritmos implementados manualmente sobre a coleção de sessões de recarga.

Por que este módulo existe separado
-----------------------------------
`session_manager` cuida do ciclo de vida de uma sessão; `power_manager`
distribui potência; aqui só há algoritmo puro sobre uma coleção — nenhuma
regra de negócio, nenhum acesso a banco, nenhuma rota. Isolado assim, o
algoritmo pode ser lido sem abrir o resto do sistema, e um teste consegue
varrer *este arquivo* para garantir que ninguém troque as implementações
manuais pela ordenação nativa mais tarde.

Dependências: apenas a biblioteca padrão e `models`. Este módulo não conhece
Flask, não conhece SQLite e não importa `session_manager` — é por isso que o
`menu.py` roda sem nenhuma dependência externa instalada.

Onde o sistema usa cada algoritmo
---------------------------------
    busca_sequencial  → SessionManager.get_session (toda consulta de sessão)
                        menu.py, opção 3
    busca_binaria     → menu.py, opção 3 (contraste com a sequencial)
    insertion_sort    → PowerManager.rebalance (toda liberação de conector)
                        menu.py, opção 4
    bubble_sort       → menu.py, opção 4
    estatisticas      → menu.py, opção 5

Contagem de comparações
-----------------------
Todos os quatro algoritmos devolvem quantas comparações realizaram. É o que
alimenta a opção 6 do menu ("Comparar algoritmos") e o que permite conferir,
na prática, que o custo bate com a fórmula fechada de cada um.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from models import ChargingSession

# ---------------------------------------------------------------------------
# Tipos auxiliares
# ---------------------------------------------------------------------------

# Extrator de chave: recebe uma sessão e devolve o valor pelo qual comparar.
# O valor precisa ser ordenável contra os demais (int, float ou str).
Chave = Callable[[ChargingSession], Any]


def _por_numero(sessao: ChargingSession) -> int:
    """Chave padrão de busca: o ID inteiro e sequencial da sessão."""
    return sessao.numero


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------

def busca_sequencial(colecao: List[ChargingSession],
                     alvo: Any,
                     chave: Chave = _por_numero) -> Tuple[int, int]:
    """
    Busca sequencial (linear) — percorre a coleção do início ao fim.

    Args:
        colecao : lista de sessões, em qualquer ordem
        alvo    : valor procurado
        chave   : extrator do valor a comparar (padrão: `numero`)

    Returns:
        (índice do elemento, comparações realizadas) — índice -1 se ausente.

    Complexidade: **O(n)**.

    De onde vem o crescimento: do laço `for indice in range(len(colecao))`,
    que no pior caso (elemento ausente, ou último da lista) executa uma
    comparação por elemento. Dobrar o número de sessões dobra o trabalho.
    Melhor caso O(1), quando o alvo é o primeiro elemento.

    Não exige pré-condição nenhuma — é o que a torna a busca usada em
    produção pelo `SessionManager.get_session`, onde a lista de sessões está
    em ordem de criação e não em ordem de ID.
    """
    comparacoes = 0
    for indice in range(len(colecao)):
        comparacoes += 1
        if chave(colecao[indice]) == alvo:
            return indice, comparacoes
    return -1, comparacoes


def busca_binaria(colecao: List[ChargingSession],
                  alvo: Any,
                  chave: Chave = _por_numero) -> Tuple[int, int]:
    """
    Busca binária — divide o intervalo de procura pela metade a cada passo.

    PRÉ-CONDIÇÃO: `colecao` ordenada de forma crescente pela mesma `chave`
    usada aqui. Sobre lista desordenada o resultado é indefinido — quem chama
    é responsável por ordenar antes (o menu ordena com `insertion_sort` e
    avisa isso na tela).

    Args:
        colecao : lista de sessões ORDENADA pela chave
        alvo    : valor procurado
        chave   : extrator do valor a comparar (padrão: `numero`)

    Returns:
        (índice do elemento, comparações realizadas) — índice -1 se ausente.

    Complexidade: **O(log n)**.

    De onde vem o crescimento: do laço `while inicio <= fim`, cujo intervalo
    de procura (`fim - inicio`) é dividido por 2 em cada iteração. Para chegar
    de n a 1 dividindo por 2 são necessários log₂(n) passos — com 180 sessões,
    no máximo 8 comparações contra as 180 da busca sequencial. É o contraste
    prático entre uma busca linear e uma logarítmica.
    """
    comparacoes = 0
    inicio = 0
    fim = len(colecao) - 1

    while inicio <= fim:
        meio = (inicio + fim) // 2
        comparacoes += 1
        valor = chave(colecao[meio])
        if valor == alvo:
            return meio, comparacoes
        if valor < alvo:
            inicio = meio + 1
        else:
            fim = meio - 1

    return -1, comparacoes


# ---------------------------------------------------------------------------
# Ordenação
# ---------------------------------------------------------------------------

def bubble_sort(colecao: List[ChargingSession], chave: Chave) -> int:
    """
    Bubble sort — compara pares vizinhos e troca os que estão fora de ordem,
    repetindo até que o maior elemento tenha "borbulhado" para o fim.

    Ordena **in-place** (a lista recebida é modificada) e é **estável**:
    sessões com a mesma chave preservam a ordem relativa original.

    Args:
        colecao : lista a ordenar, modificada no lugar
        chave   : extrator do valor de comparação

    Returns:
        Comparações realizadas — exatamente **n(n-1)/2**.

    Complexidade: **O(n²)** em todos os casos.

    De onde vem o crescimento: dos dois laços `for` aninhados. O externo roda
    n-1 vezes; o interno roda n-1-i vezes. A soma é n(n-1)/2 comparações, um
    polinômio de grau 2 — daí o n².

    Decisão deliberada: **sem parada antecipada.** A otimização clássica
    (interromper quando uma passada não troca nada) derrubaria o melhor caso
    para O(n), mas quebraria a igualdade exata com n(n-1)/2 que a tabela do
    relatório usa para provar a fórmula. Quem precisa de melhor caso linear
    usa `insertion_sort`, que oferece isso por construção.
    """
    comparacoes = 0
    n = len(colecao)
    for i in range(n - 1):
        for j in range(n - 1 - i):
            comparacoes += 1
            if chave(colecao[j]) > chave(colecao[j + 1]):
                colecao[j], colecao[j + 1] = colecao[j + 1], colecao[j]
    return comparacoes


def insertion_sort(colecao: List[ChargingSession], chave: Chave) -> int:
    """
    Insertion sort — percorre a lista da esquerda para a direita e insere cada
    elemento na posição correta dentro da parte já ordenada, deslocando os
    maiores uma casa para a direita.

    Ordena **in-place** e é **estável** (a parada usa `<=`, então iguais não
    se cruzam). A estabilidade importa no `PowerManager.rebalance`: sessões
    com a mesma potência alocada são restauradas na ordem em que entraram,
    o que torna a decisão reproduzível.

    Args:
        colecao : lista a ordenar, modificada no lugar
        chave   : extrator do valor de comparação

    Returns:
        Comparações realizadas — entre **n-1** e **n(n-1)/2**.

    Complexidade: **O(n²)** no pior caso, **O(n)** no melhor.

    De onde vem o crescimento: do laço `while j >= 0` aninhado no `for`. Ele
    recua enquanto encontra elementos maiores que o atual. Com a lista em
    ordem inversa, o i-ésimo elemento recua i posições e a soma volta a ser
    n(n-1)/2. Com a lista já ordenada, o `while` para na primeira comparação
    e o total cai para n-1 — linear.

    É esse melhor caso que o justifica no `rebalance`: a cada liberação de
    conector a lista de sessões em throttle chega quase ordenada, e são no
    máximo 5 elementos (MAX_CHARGERS_PER_STATION), ou seja ≤ 10 comparações.
    """
    comparacoes = 0
    for i in range(1, len(colecao)):
        atual = colecao[i]
        valor = chave(atual)
        j = i - 1
        while j >= 0:
            comparacoes += 1
            if chave(colecao[j]) <= valor:
                break
            colecao[j + 1] = colecao[j]
            j -= 1
        colecao[j + 1] = atual
    return comparacoes


# ---------------------------------------------------------------------------
# Critérios de ordenação oferecidos ao usuário
# ---------------------------------------------------------------------------

CRITERIOS: Dict[str, Tuple[str, Chave]] = {
    "1": ("ID",                lambda s: s.numero),
    "2": ("Energia consumida", lambda s: s.energy_kwh),
    "3": ("Custo da sessão",   lambda s: s.total_cost_brl),
    "4": ("Tempo de recarga",  lambda s: s.duration_minutes),
}


# ---------------------------------------------------------------------------
# Estatísticas
# ---------------------------------------------------------------------------

def estatisticas(colecao: List[ChargingSession]) -> Dict[str, float]:
    """
    Calcula os indicadores agregados da coleção em UMA única passada.

    Os seis indicadores principais, mais quatro de apoio:
        total_sessoes         : quantas sessões estão armazenadas
        energia_total_kwh     : energia entregue somada
        faturamento_brl       : receita somada
        ticket_medio_brl      : faturamento ÷ total de sessões
        maior_consumo_kwh     : maior energia individual
        menor_consumo_kwh     : menor energia individual
        tempo_total_min       : tempo de recarga somado (apoio)
        energia_media_kwh     : energia ÷ total de sessões (apoio)
        numero_maior_consumo  : ID da sessão de maior consumo (apoio)
        numero_menor_consumo  : ID da sessão de menor consumo (apoio)

    Complexidade: O(n) — um laço, sem chamadas de agregação nativas. Máximo e
    mínimo são acompanhados na mesma varredura em vez de duas passadas
    adicionais.

    Coleção vazia devolve zeros em todos os campos. Isso é proposital: as
    funções nativas de mínimo e máximo levantam `ValueError` em sequência
    vazia, e abrir o menu com o banco vazio é uma situação normal, não um
    erro que deva derrubar o programa.
    """
    total = len(colecao)
    if total == 0:
        return {
            "total_sessoes":        0,
            "energia_total_kwh":    0.0,
            "faturamento_brl":      0.0,
            "ticket_medio_brl":     0.0,
            "maior_consumo_kwh":    0.0,
            "menor_consumo_kwh":    0.0,
            "tempo_total_min":      0.0,
            "energia_media_kwh":    0.0,
            "numero_maior_consumo": 0,
            "numero_menor_consumo": 0,
        }

    energia_total = 0.0
    faturamento = 0.0
    tempo_total = 0.0
    maior = colecao[0].energy_kwh
    menor = colecao[0].energy_kwh
    numero_maior = colecao[0].numero
    numero_menor = colecao[0].numero

    for sessao in colecao:
        energia = sessao.energy_kwh
        energia_total += energia
        faturamento += sessao.total_cost_brl
        tempo_total += sessao.duration_minutes
        if energia > maior:
            maior = energia
            numero_maior = sessao.numero
        if energia < menor:
            menor = energia
            numero_menor = sessao.numero

    return {
        "total_sessoes":        total,
        "energia_total_kwh":    round(energia_total, 3),
        "faturamento_brl":      round(faturamento, 2),
        "ticket_medio_brl":     round(faturamento / total, 2),
        "maior_consumo_kwh":    round(maior, 3),
        "menor_consumo_kwh":    round(menor, 3),
        "tempo_total_min":      round(tempo_total, 1),
        "energia_media_kwh":    round(energia_total / total, 3),
        "numero_maior_consumo": numero_maior,
        "numero_menor_consumo": numero_menor,
    }


# ---------------------------------------------------------------------------
# Apoio ao cadastro (validação de ID duplicado)
# ---------------------------------------------------------------------------

def numero_existe(colecao: List[ChargingSession], numero: int) -> bool:
    """
    Informa se já existe sessão com o ID informado.

    Reaproveita `busca_sequencial` de propósito: procurar um ID duplicado é,
    literalmente, uma busca. Escrever um segundo laço aqui duplicaria o
    algoritmo.

    Complexidade: O(n), herdada da busca sequencial.
    """
    indice, _ = busca_sequencial(colecao, numero)
    return indice >= 0


def proximo_numero(colecao: List[ChargingSession]) -> int:
    """
    Devolve o próximo ID livre: o maior `numero` presente mais 1.

    Usa varredura explícita em vez da função nativa de máximo para manter
    este módulo inteiro no mesmo padrão — laço à mão sobre a lista.

    Complexidade: O(n). Coleção vazia devolve 1.
    """
    maior = 0
    for sessao in colecao:
        if sessao.numero > maior:
            maior = sessao.numero
    return maior + 1
