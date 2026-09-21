# =============================================================================
#  ChargeGrid Intelligence — Gerenciador de Sessões
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
SessionManager: responsável pelo ciclo de vida completo das sessões.

Responsabilidades:
    - Criar, consultar, atualizar e encerrar sessões de recarga
    - Manter o estado em memória (lista de sessões, na ordem de criação)
    - Expor métricas agregadas usadas pelo PowerManager e PricingEngine
    - NÃO decide potência nem calcula tarifas (delegado a outros módulos)

Arquitetura:
    O SessionManager é o hub central. PowerManager e PricingEngine recebem
    uma referência a ele via injeção de dependência no main.py, garantindo
    que a lógica fique separada sem acoplamento direto entre os módulos.

Persistência (Sprint 3):
    Sessões ATIVAS continuam na lista em memória — é o estado quente, lido e
    escrito a cada polling de 5 segundos. Sessões ENCERRADAS e pagas são
    arquivadas em SQLite pela camada web (ver db.py), alimentando relatório,
    exportação em CSV e as análises estatísticas.

    Migrar também o estado quente para o banco é possível sem tocar em quem
    consome este módulo: a interface pública (create, get, update_*, finish,
    list_*) foi mantida estável justamente para isso.
"""

import datetime
import logging
from typing import Dict, List, Optional

import algoritmos
from models import ChargingSession, SessionStatus, UserType

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MAX_CHARGERS_PER_STATION: int = 5   # máximo de conectores por posto
POTENCIA_NOMINAL_KW: float   = 11.0  # GW11K-HCA-20


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """
    Gerenciador de sessões de recarga simultâneas.

    Suporta múltiplos postos (stations) e múltiplos carregadores por posto.
    O estado é mantido em duas estruturas, cada uma escolhida pelo que a
    disciplina de Estruturas de Dados chamaria de acesso dominante:

        _sessions : List[ChargingSession] — todas as sessões, na ordem em que
            entraram. É uma **lista** porque a coleção de sessões é percorrida
            (relatório, estatísticas, ordenação por critério escolhido pelo
            usuário) muito mais do que acessada por chave, e porque a ordem de
            chegada é informação de negócio: é ela que dá o ID sequencial.
            A consulta por ID usa `algoritmos.busca_sequencial`.

        _chargers : {charger_id → session_id | None} — ocupação dos conectores.
            Continua **dicionário** de propósito: não é a coleção de sessões,
            é uma tabela fixa de 15 conectores físicos (3 postos × 5), com
            chave conhecida e imutável, escrita e lida a cada polling. Trocar
            por lista só acrescentaria varredura sem ganho nenhum.
    """

    def __init__(self) -> None:
        self._sessions: List[ChargingSession] = []
        # Carregadores pré-cadastrados: 5 por posto, 3 postos = 15 total
        self._chargers: Dict[str, Optional[str]] = {
            f"P{p}-C{c}": None
            for p in range(1, 4)
            for c in range(1, MAX_CHARGERS_PER_STATION + 1)
        }
        logger.info("SessionManager inicializado com %d carregadores.", len(self._chargers))

    # ------------------------------------------------------------------
    # Consulta de carregadores
    # ------------------------------------------------------------------

    def list_chargers(self) -> Dict[str, Optional[str]]:
        """Retorna o dicionário completo de carregadores e suas sessões."""
        return dict(self._chargers)

    def available_chargers(self) -> List[str]:
        """Lista os IDs de carregadores sem sessão ativa."""
        return [cid for cid, sid in self._chargers.items() if sid is None]

    def is_charger_available(self, charger_id: str) -> bool:
        """Verifica se um carregador específico está livre."""
        if charger_id not in self._chargers:
            raise ValueError(f"Carregador '{charger_id}' não existe no sistema.")
        return self._chargers[charger_id] is None

    # ------------------------------------------------------------------
    # Criação de sessão
    # ------------------------------------------------------------------

    def create_session(
        self,
        charger_id: str,
        vehicle_id: str,
        user_name: str,
        user_type: UserType,
        requested_power_kw: float = POTENCIA_NOMINAL_KW,
        owner: str = "",
        numero: Optional[int] = None,
    ) -> ChargingSession:
        """
        Inicia uma nova sessão de recarga em um carregador disponível.

        Args:
            charger_id          : ID do carregador (ex.: "P1-C3")
            vehicle_id          : placa ou identificador do veículo
            user_name           : nome do motorista
            user_type           : categoria tarifária (UserType)
            requested_power_kw  : potência solicitada (padrão: nominal do HCA)
            owner               : chave da conta dona da sessão (vazio = criada
                                  pelo operador no painel administrativo)
            numero              : ID sequencial a atribuir; None = próximo livre

        Returns:
            ChargingSession recém-criada (status = PREPARING)

        Raises:
            ValueError  : carregador inválido ou ocupado
            ValueError  : potência solicitada fora do intervalo permitido
        """
        if charger_id not in self._chargers:
            raise ValueError(f"Carregador '{charger_id}' não cadastrado.")

        if self._chargers[charger_id] is not None:
            raise ValueError(
                f"Carregador '{charger_id}' está ocupado "
                f"(sessão: {self._chargers[charger_id]})."
            )

        # Valida faixa de potência (mínima do HCA trifásico: 4.2 kW)
        if not (4.2 <= requested_power_kw <= POTENCIA_NOMINAL_KW):
            raise ValueError(
                f"Potência solicitada {requested_power_kw} kW fora do intervalo "
                f"permitido [4.2, {POTENCIA_NOMINAL_KW}] kW."
            )

        # Extrai station_id do charger_id (formato "P1-C3" → "P1")
        station_id = charger_id.split("-")[0]

        session = ChargingSession(
            charger_id=charger_id,
            station_id=station_id,
            vehicle_id=vehicle_id,
            user_name=user_name,
            user_type=user_type,
            requested_power_kw=requested_power_kw,
            owner=owner,
            status=SessionStatus.PREPARING,
            numero=(algoritmos.proximo_numero(self._sessions)
                    if numero is None else numero),
        )

        self._sessions.append(session)
        self._chargers[charger_id] = session.session_id

        logger.info(
            "Sessão criada: %s | Carregador: %s | Veículo: %s | Usuário: %s (%s)",
            session.session_id, charger_id, vehicle_id, user_name, user_type.value,
        )
        return session

    def registrar_historico(self, sessao: ChargingSession) -> ChargingSession:
        """
        Anexa uma sessão JÁ ENCERRADA à coleção, sem tocar na ocupação de
        conectores.

        Por que não reusar `create_session`: aquele método inicia uma recarga
        AGORA — valida o conector, recusa se estiver ocupado e marca a
        ocupação. São 15 conectores no sistema; carregar algumas centenas de
        registros históricos por ali quebraria no 16º, e registrar um fato
        passado não deve reservar hardware no presente. São duas operações
        distintas e cada uma tem seu método.

        É o caminho usado por `db.carregar_sessoes()` (histórico do SQLite) e
        pela opção "Nova sessão de recarga" do menu de terminal.

        Args:
            sessao : sessão pronta, com `numero` já atribuído

        Returns:
            A própria sessão, agora na coleção.

        Raises:
            ValueError : se `numero` já existir na coleção. A checagem usa
                         `algoritmos.numero_existe`, ou seja, a mesma busca
                         sequencial.
        """
        if algoritmos.numero_existe(self._sessions, sessao.numero):
            raise ValueError(f"Já existe sessão com ID {sessao.numero}.")
        self._sessions.append(sessao)
        return sessao

    # ------------------------------------------------------------------
    # Atualização de sessão
    # ------------------------------------------------------------------

    def start_charging(self, session_id: str, allocated_power_kw: float,
                       tariff_kwh: float) -> ChargingSession:
        """
        Transita a sessão de PREPARING → CHARGING após alocação de potência.

        Args:
            session_id          : ID da sessão
            allocated_power_kw  : potência concedida pelo PowerManager
            tariff_kwh          : tarifa calculada pela PricingEngine

        Returns:
            Sessão atualizada
        """
        session = self._get_or_raise(session_id)
        session.allocated_power_kw = allocated_power_kw
        session.tariff_kwh = tariff_kwh
        session.status = SessionStatus.CHARGING
        # Inicia o relógio de energia: a partir daqui a energia é acumulada
        # com base no tempo real decorrido (ver accrue_energy).
        session.last_energy_update = datetime.datetime.now()
        logger.info(
            "Carregamento iniciado: %s | %.1f kW | R$ %.4f/kWh",
            session_id, allocated_power_kw, tariff_kwh,
        )
        return session

    def accrue_energy(self, sessao: "ChargingSession | str"
                      ) -> Optional[ChargingSession]:
        """
        Acumula energia com base no TEMPO REAL decorrido desde a última
        contabilização, à potência atualmente alocada.

        Esta é a forma correta de acumular energia na camada web: é
        idempotente em relação ao número de chamadas. Chamar duas vezes
        em sequência rápida acrescenta quase nada (pouco tempo decorreu);
        múltiplas abas ou refreshes convergem para o mesmo valor real,
        pois cada chamada contabiliza apenas o intervalo desde a anterior.

        Energia (kWh) = potência (kW) × Δt (horas)

        Aceita a própria sessão ou o ID dela. Quem já tem o objeto em mãos
        — o laço de polling da camada web, os métodos deste módulo — passa o
        objeto e evita uma busca sequencial redundante a cada chamada.

        Args:
            sessao : a sessão, ou o ID dela

        Returns:
            Sessão atualizada (inalterada se inativa ou sem relógio iniciado)
        """
        session = (sessao if isinstance(sessao, ChargingSession)
                   else self.get_session(sessao))
        if session is None or not session.is_active:
            return session  # silencioso: nada a acumular

        agora = datetime.datetime.now()
        referencia = session.last_energy_update or session.start_time
        delta_horas = (agora - referencia).total_seconds() / 3600.0

        if delta_horas <= 0:
            return session  # relógio sem avanço (chamadas concorrentes)

        delta_kwh = session.allocated_power_kw * delta_horas
        session.energy_kwh = round(session.energy_kwh + delta_kwh, 4)
        session.total_cost_brl = round(
            session.energy_kwh * session.tariff_kwh, 2
        )
        session.last_energy_update = agora
        return session

    def update_energy(self, session_id: str, delta_kwh: float) -> ChargingSession:
        """
        Adiciona uma quantidade FIXA de energia à sessão e recalcula o custo.

        Uso destinado a simulações determinísticas (demo do main.py e testes),
        onde se quer controlar exatamente quanta energia foi consumida sem
        depender do relógio. A camada web usa accrue_energy (tempo real).

        Args:
            session_id : ID da sessão
            delta_kwh  : energia a adicionar neste intervalo (kWh)

        Returns:
            Sessão atualizada
        """
        session = self._get_or_raise(session_id)
        if not session.is_active:
            raise RuntimeError(
                f"Tentativa de atualizar energia em sessão inativa: {session_id}"
            )
        session.energy_kwh = round(session.energy_kwh + delta_kwh, 4)
        session.total_cost_brl = round(
            session.energy_kwh * session.tariff_kwh, 2
        )
        return session

    def throttle_session(self, session_id: str,
                         new_power_kw: float) -> ChargingSession:
        """
        Reduz a potência de uma sessão ativa (controle dinâmico de demanda).

        Transita o status para THROTTLED para sinalizar ao Modbus
        que o registrador 10029 foi reescrito.
        """
        session = self._get_or_raise(session_id)
        # Contabiliza a energia consumida à potência ATUAL antes de alterá-la,
        # para que o intervalo até agora não seja cobrado à nova potência.
        self.accrue_energy(session)
        old_power = session.allocated_power_kw
        session.allocated_power_kw = round(new_power_kw, 2)
        session.status = SessionStatus.THROTTLED
        logger.warning(
            "Throttle aplicado: %s | %.1f kW → %.1f kW",
            session_id, old_power, new_power_kw,
        )
        return session

    def restore_session(self, session_id: str,
                        power_kw: float) -> ChargingSession:
        """
        Restaura potência de uma sessão THROTTLED após liberação de capacidade.

        O status só transita para CHARGING quando a potência concedida atinge
        (com margem de 0.1 kW) a potência solicitada originalmente.
        Se a restauração for parcial — capacidade disponível permite aumentar
        mas não atingir o valor original — a sessão permanece THROTTLED com
        a nova potência mais alta. Isso evita o bug em que sessões exibem
        CHARGING mesmo recebendo menos do que pediram.
        """
        session = self._get_or_raise(session_id)
        # Contabiliza energia à potência reduzida antes de mudar a alocação.
        self.accrue_energy(session)
        session.allocated_power_kw = round(power_kw, 2)
        # Transita para CHARGING apenas se a potência foi totalmente restaurada.
        if session.allocated_power_kw >= session.requested_power_kw - 0.1:
            session.status = SessionStatus.CHARGING
            logger.info(
                "Sessão restaurada (CHARGING): %s | %.1f kW", session_id, power_kw
            )
        else:
            session.status = SessionStatus.THROTTLED
            logger.info(
                "Sessão parcialmente restaurada (THROTTLED): %s | %.1f kW "
                "(solicitado: %.1f kW)",
                session_id, power_kw, session.requested_power_kw,
            )
        return session

    # ------------------------------------------------------------------
    # Encerramento
    # ------------------------------------------------------------------

    def finish_session(self, session_id: str,
                       status: SessionStatus = SessionStatus.FINISHED,
                       liberar: bool = True,
                       ) -> ChargingSession:
        """
        Encerra uma sessão, libera o carregador e aplica taxa mínima se necessário.

        Idempotente: chamar duas vezes na mesma sessão retorna o objeto
        inalterado sem reprocessar taxa mínima nem tentar liberar o
        carregador novamente.

        Args:
            session_id : ID da sessão
            status     : FINISHED (padrão) ou FAULTED
            liberar    : False mantém o conector ocupado após o encerramento.
                         É o caso do fluxo do motorista: parar o fluxo de
                         energia não desocupa a vaga — o carro só sai depois
                         de pagar (ver release_charger).

        Returns:
            Sessão encerrada
        """
        from pricing_engine import TAXA_MINIMA_SESSAO  # importação local evita circular

        session = self._get_or_raise(session_id)

        # Guarda de idempotência: se já foi encerrada, retorna sem reprocessar
        if session.status in (SessionStatus.FINISHED, SessionStatus.FAULTED):
            logger.debug(
                "finish_session chamado em sessão já encerrada: %s (%s) — ignorado.",
                session_id, session.status.value,
            )
            return session

        # Contabiliza energia até o momento do encerramento (tempo real)
        self.accrue_energy(session)

        session.end_time = datetime.datetime.now()
        session.status = status

        # Aplica taxa mínima (herdada do Sprint 1)
        if session.total_cost_brl < TAXA_MINIMA_SESSAO:
            session.total_cost_brl = TAXA_MINIMA_SESSAO

        # Libera o carregador
        if liberar:
            self._chargers[session.charger_id] = None

        logger.info(
            "Sessão encerrada: %s | Status: %s | Energia: %.3f kWh | Custo: R$ %.2f"
            " | conector %s",
            session_id, status.value, session.energy_kwh, session.total_cost_brl,
            "liberado" if liberar else "retido",
        )
        return session

    def release_charger(self, session_id: str) -> None:
        """
        Desocupa o conector de uma sessão já encerrada.

        Separado de `finish_session` porque os dois eventos são distintos no
        mundo real: o carregamento termina quando a energia para, e a vaga só
        vaga quando o carro é desplugado — aqui, quando a sessão é paga ou
        quando a equipe do posto libera o conector manualmente.
        """
        session = self._get_or_raise(session_id)
        if self._chargers.get(session.charger_id) == session_id:
            self._chargers[session.charger_id] = None
            logger.info("Conector liberado: %s (sessão %s)",
                        session.charger_id, session_id)

    # ------------------------------------------------------------------
    # Consultas agregadas
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Optional[ChargingSession]:
        """
        Retorna a sessão ou None se não encontrada.

        Usa **busca sequencial** (`algoritmos.busca_sequencial`) sobre a
        lista, com `session_id` como chave. Toda consulta de sessão da camada
        web passa por aqui.

        Complexidade: O(n). O n relevante é pequeno por construção física —
        são 15 conectores, logo no máximo 15 sessões ativas simultâneas; as
        encerradas e pagas saem da memória para o SQLite. Nenhuma consulta da
        camada web percorre o histórico inteiro.
        """
        indice, _ = algoritmos.busca_sequencial(
            self._sessions, session_id, chave=lambda s: s.session_id
        )
        return self._sessions[indice] if indice >= 0 else None

    @property
    def sessions(self) -> List[ChargingSession]:
        """
        A lista VIVA de sessões, na ordem de armazenamento.

        Diferente de `list_all()`, que devolve uma cópia: aqui o chamador
        recebe a própria lista. É o que permite ao menu de terminal ordenar a
        coleção **in-place** e ver o efeito persistir entre as opções —
        ordenar por energia na opção 4 e depois listar na opção 2 mostra a
        nova ordem.

        Quem só quer ler sem risco de alterar a ordem usa `list_all()`.
        """
        return self._sessions

    def list_active(self) -> List[ChargingSession]:
        """Lista todas as sessões em andamento (CHARGING ou THROTTLED)."""
        return [s for s in self._sessions if s.is_active]

    def list_all(self) -> List[ChargingSession]:
        """Lista todas as sessões (ativas e encerradas)."""
        return list(self._sessions)

    def total_allocated_power_kw(self) -> float:
        """Soma da potência alocada em todas as sessões ativas."""
        return round(sum(s.allocated_power_kw for s in self.list_active()), 2)

    def active_count(self) -> int:
        """Número de sessões atualmente ativas."""
        return len(self.list_active())

    def occupancy_ratio(self) -> float:
        """
        Taxa de ocupação da rede [0.0, 1.0].
        Usada pela PricingEngine para o eixo de tarifação por demanda.

        Conta CONECTORES ocupados, não sessões ativas (#B44). Desde que uma
        recarga encerrada segura o conector até o pagamento, os dois números
        divergem: com 3 carros carregando e 1 conector retido esperando
        pagamento, 4 de 5 vagas estão indisponíveis, mas só 3 sessões estão
        ativas. Contar sessões subestimava a demanda, barateava a tarifa e
        contradizia o "1 disponível" que a tela do posto mostrava.

        Deriva do total real de conectores cadastrados no sistema em vez
        de um valor hardcoded — se MAX_CHARGERS_PER_STATION ou o range
        de postos mudar, a ocupação continua correta automaticamente.
        """
        total = len(self._chargers)
        ocupados = sum(1 for sid in self._chargers.values() if sid is not None)
        return ocupados / max(total, 1)

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _get_or_raise(self, session_id: str) -> ChargingSession:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Sessão '{session_id}' não encontrada.")
        return session
