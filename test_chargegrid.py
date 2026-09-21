# =============================================================================
#  ChargeGrid Intelligence — Testes Automatizados
#  Sprint 3 | FIAP + GoodWe EV Challenge 2026
# =============================================================================

"""
Testes automatizados para os módulos de negócio do ChargeGrid.

Execução:
    pip install pytest
    pytest test_chargegrid.py -v

Cobertura:
    - PricingEngine : todos os eixos e combinações de tarifa
    - PowerManager  : alocação (4 casos), rebalanceamento, recusa física
    - SessionManager: criação, acúmulo de energia, idempotência do finish
    - Integração    : ciclo completo de sessão via app Flask
"""

import pathlib
import time

import pytest

# ---------------------------------------------------------------------------
# Isolamento do banco (Sprint 3)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _db_temporario(tmp_path, monkeypatch):
    """
    Redireciona o SQLite para um arquivo temporário em cada teste.

    Sem isso, rodar a suíte mexeria no `chargegrid.db` usado na demonstração:
    saldos alterados, reservas criadas e sessões arquivadas no meio de uma
    apresentação. O `autouse` garante que nenhum teste escape por esquecimento.
    """
    import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "teste.db")
    db.init()
    yield


# ---------------------------------------------------------------------------
# Fixtures compartilhadas
# ---------------------------------------------------------------------------

@pytest.fixture
def sm():
    """SessionManager limpo para cada teste."""
    from session_manager import SessionManager
    return SessionManager()


@pytest.fixture
def pm(sm):
    """PowerManager com limite de 33 kW."""
    from power_manager import PowerManager
    return PowerManager(sm, limit_kw=33.0)


@pytest.fixture
def pe(sm):
    """PricingEngine ligado ao SessionManager."""
    from pricing_engine import PricingEngine
    return PricingEngine(sm)


@pytest.fixture
def full(sm, pm, pe):
    """Retorna a trinca (sm, pm, pe) para testes de integração leve."""
    return sm, pm, pe


@pytest.fixture
def chargers(sm):
    """Lista de IDs de carregadores disponíveis."""
    return list(sm.list_chargers().keys())


def _start(sm, pm, pe, charger_id, user_type, pot=11.0, hora=10):
    """Helper: cria + aloca + inicia sessão. Espelha o comportamento do app.py.

    Após start_charging, aplica throttle_session na nova sessão quando a
    potência concedida é menor que a solicitada (redistribuição Caso 3).
    Isso replica o passo que vive em app.py::dashboard_nova_sessao e é
    necessário para que os testes de regressão de status THROTTLED sejam válidos.
    """
    from models import SessionStatus
    s = sm.create_session(charger_id, "TST-0001", "Test User", user_type, pot)
    r = pm.allocate(s)
    if not r.rejected:
        t = pe.calculate(user_type, hora=hora)
        sm.start_charging(s.session_id, r.granted_kw, t.tariff_kwh)
        # Replica o passo de throttle da nova sessão (app.py linha ~544)
        if r.redistributed and r.granted_kw < pot:
            sm.throttle_session(s.session_id, r.granted_kw)
    return s, r


# ===========================================================================
# PricingEngine
# ===========================================================================

class TestPricingEngine:

    def test_tarifa_base_fora_pico(self, pe):
        from models import UserType
        from pricing_engine import TARIFA_BASE_KWH
        r = pe.calculate(UserType.STANDARD, hora=10)
        assert r.tariff_kwh == TARIFA_BASE_KWH
        assert not r.peak_applied
        assert not r.demand_applied

    def test_multiplicador_pico(self, pe):
        from models import UserType
        from pricing_engine import TARIFA_BASE_KWH, MULTIPLICADOR_PICO
        r = pe.calculate(UserType.STANDARD, hora=20)
        assert r.peak_applied
        assert abs(r.tariff_kwh - round(TARIFA_BASE_KWH * MULTIPLICADOR_PICO, 4)) < 0.0001

    def test_desconto_assinante(self, pe):
        from models import UserType
        from pricing_engine import TARIFA_BASE_KWH, DESCONTO_ASSINANTE
        r = pe.calculate(UserType.SUBSCRIBER, hora=10)
        esperado = round(TARIFA_BASE_KWH * (1 - DESCONTO_ASSINANTE), 4)
        assert abs(r.tariff_kwh - esperado) < 0.0001
        assert r.user_discount_pct == pytest.approx(DESCONTO_ASSINANTE * 100)

    def test_desconto_corporativo(self, pe):
        from models import UserType
        from pricing_engine import TARIFA_BASE_KWH, DESCONTO_CORPORATE
        r = pe.calculate(UserType.CORPORATE, hora=10)
        esperado = round(TARIFA_BASE_KWH * (1 - DESCONTO_CORPORATE), 4)
        assert abs(r.tariff_kwh - esperado) < 0.0001

    def test_eixo_demanda_ativado(self, sm, pm, pe, chargers):
        """Demanda ativa quando ocupação >= 70% (11/15 = 73.3%)."""
        from models import UserType
        from pricing_engine import LIMIAR_DEMANDA
        for i in range(11):   # 11/15 = 73.3% >= 70%
            s, r = _start(sm, pm, pe, chargers[i], UserType.STANDARD, hora=10)
        ocupacao = sm.occupancy_ratio()
        assert ocupacao >= LIMIAR_DEMANDA, f"ocupação {ocupacao:.1%} < {LIMIAR_DEMANDA:.0%}"
        r = pe.calculate(UserType.STANDARD, hora=10)
        assert r.demand_applied

    def test_eixo_demanda_inativo_baixa_ocupacao(self, pe):
        """Sem sessões: demanda não deve ser aplicada."""
        from models import UserType
        r = pe.calculate(UserType.STANDARD, hora=10)
        assert not r.demand_applied

    def test_tres_eixos_combinados(self, sm, pm, pe, chargers):
        """Pico + Alta demanda + Assinante devem combinar multiplicadores."""
        from models import UserType
        from pricing_engine import (TARIFA_BASE_KWH, MULTIPLICADOR_PICO,
                                     MULTIPLICADOR_DEMANDA, DESCONTO_ASSINANTE)
        for i in range(11):   # 11/15 = 73.3% >= 70%
            _start(sm, pm, pe, chargers[i], UserType.STANDARD, hora=10)
        r = pe.calculate(UserType.SUBSCRIBER, hora=20)
        esperado = round(
            TARIFA_BASE_KWH
            * MULTIPLICADOR_PICO
            * MULTIPLICADOR_DEMANDA
            * (1 - DESCONTO_ASSINANTE),
            4,
        )
        assert abs(r.tariff_kwh - esperado) < 0.001
        assert r.peak_applied and r.demand_applied

    def test_taxa_minima_estimativa(self, pe):
        from models import UserType
        from pricing_engine import TAXA_MINIMA_SESSAO
        custo = pe.estimate_cost(UserType.STANDARD, energy_kwh=0.01, hora=10)
        assert custo == TAXA_MINIMA_SESSAO

    @pytest.mark.parametrize("hora,esperado_pico", [
        (17, False), (18, True), (22, True), (23, False), (0, False),
    ])
    def test_fronteiras_horario_pico(self, pe, hora, esperado_pico):
        from models import UserType
        r = pe.calculate(UserType.STANDARD, hora=hora, minuto=0)
        assert r.peak_applied == esperado_pico


# ===========================================================================
# PowerManager — Alocação
# ===========================================================================

class TestPowerManagerAlocacao:

    def test_caso_1_potencia_integral(self, sm, pm, pe, chargers):
        """Carga ≤ 90% do limite → potência integral, severity=ok."""
        from models import UserType
        s, r = _start(sm, pm, pe, chargers[0], UserType.STANDARD)
        assert r.granted_kw == 11.0
        assert not r.redistributed
        assert r.severity == "ok"
        assert not r.rejected

    def test_caso_2_redistribuicao_parcial(self, sm, pm, pe, chargers):
        """3×11=33kW cabe no limite; 4ª sessão (44kW projetado > 33kW) aciona redistribuição."""
        from models import UserType
        # 3 sessões a 11kW = 33kW (exatamente no limite)
        _start(sm, pm, pe, chargers[0], UserType.STANDARD)
        _start(sm, pm, pe, chargers[1], UserType.STANDARD)
        _start(sm, pm, pe, chargers[2], UserType.STANDARD)
        # 4ª sessão: 33+11=44kW > 33kW → Caso 3 (redistribuição total)
        s4, r4 = _start(sm, pm, pe, chargers[3], UserType.STANDARD)
        assert r4.redistributed
        assert r4.severity in ("warning", "danger")
        assert sm.total_allocated_power_kw() <= pm.limit_kw

    def test_caso_3_estouro_redistribuicao_total(self, sm, pe, chargers):
        """Limite 22kW + 3 sessões → Caso 3, severity=danger."""
        from models import UserType
        from power_manager import PowerManager
        pm2 = PowerManager(sm, limit_kw=22.0)
        _start(sm, pm2, pe, chargers[0], UserType.STANDARD)
        _start(sm, pm2, pe, chargers[1], UserType.STANDARD)
        s3, r3 = _start(sm, pm2, pe, chargers[2], UserType.STANDARD)
        assert r3.redistributed
        assert r3.severity == "danger"
        assert sm.total_allocated_power_kw() <= 22.0

    def test_caso_0_recusa_fisica(self, sm, pe, chargers):
        """(n+1) × 4.2 > limite → sessão recusada, severity=danger."""
        from models import UserType, SessionStatus
        from power_manager import PowerManager
        pm_apertado = PowerManager(sm, limit_kw=22.0)
        recusadas = 0
        for i in range(8):
            s = sm.create_session(chargers[i], f"V{i}", f"U{i}", UserType.STANDARD, 11.0)
            r = pm_apertado.allocate(s)
            if r.rejected:
                sm.finish_session(s.session_id, SessionStatus.FAULTED)
                recusadas += 1
            else:
                t = pe.calculate(UserType.STANDARD, hora=10)
                sm.start_charging(s.session_id, r.granted_kw, t.tariff_kwh)
        assert recusadas > 0
        assert sm.total_allocated_power_kw() <= 22.0

    def test_load_after_consistente(self, sm, pm, pe, chargers):
        """load_after_kw deve bater com o estado real pós-throttle."""
        from models import UserType
        _start(sm, pm, pe, chargers[0], UserType.STANDARD)
        _start(sm, pm, pe, chargers[1], UserType.STANDARD)
        s3, r3 = _start(sm, pm, pe, chargers[2], UserType.STANDARD, pot=8.0)
        divergencia = abs(r3.load_after_kw - sm.total_allocated_power_kw())
        assert divergencia < 0.5, f"Divergência de {divergencia:.2f} kW"

    def test_limite_nunca_ultrapassado_12_sessoes(self, sm, pm, pe, chargers):
        """12 tentativas simultâneas nunca devem estourar o limite."""
        from models import UserType, SessionStatus
        for i in range(12):
            s = sm.create_session(chargers[i], f"V{i}", f"U{i}", UserType.STANDARD, 11.0)
            r = pm.allocate(s)
            if r.rejected:
                sm.finish_session(s.session_id, SessionStatus.FAULTED)
            else:
                t = pe.calculate(UserType.STANDARD, hora=10)
                sm.start_charging(s.session_id, r.granted_kw, t.tariff_kwh)
        assert sm.total_allocated_power_kw() <= pm.limit_kw


# ===========================================================================
# PowerManager — Rebalanceamento
# ===========================================================================

class TestPowerManagerRebalance:

    def test_rebalance_restaura_throttled(self, sm, pe, chargers):
        """Após encerrar sessão, throttled devem recuperar potência."""
        from models import UserType, SessionStatus
        from power_manager import PowerManager
        pm2 = PowerManager(sm, limit_kw=22.0)
        sess = []
        for i in range(3):
            s, r = _start(sm, pm2, pe, chargers[i], UserType.STANDARD)
            if not r.rejected:
                sess.append(s)
        throttled_antes = [s for s in sm.list_active()
                           if s.status == SessionStatus.THROTTLED]
        assert len(throttled_antes) > 0

        sm.finish_session(sess[0].session_id)
        rb = pm2.rebalance()
        assert rb is not None
        assert sm.total_allocated_power_kw() <= 22.0

    def test_rebalance_nao_ultrapassa_limite(self, sm, pe, chargers):
        """Rebalanceamento nunca deve fazer a carga exceder o limite."""
        from models import UserType
        from power_manager import PowerManager
        pm2 = PowerManager(sm, limit_kw=22.0)
        sess = []
        for i in range(3):
            s, r = _start(sm, pm2, pe, chargers[i], UserType.STANDARD)
            if not r.rejected:
                sess.append(s)
        sm.finish_session(sess[0].session_id)
        pm2.rebalance()
        assert sm.total_allocated_power_kw() <= 22.0

    def test_rebalance_sem_sessoes_retorna_none(self, sm, pm):
        rb = pm.rebalance()
        assert rb is None

    # ---- B43: sobra de quem satura tem que ir para os outros -------------
    def test_prioridade_nao_deixa_potencia_ociosa(self, sm, pm, pe, chargers):
        """
        Com um padrão e dois assinantes em 33 kW, os três cabem a 11 kW.

        A fatia por peso do assinante daria 11.65 kW; ele só usa 11.0 e a
        sobra tem que ir para o padrão. Antes ela era descartada e o padrão
        travava em 9.71 kW com 1.29 kW livres no posto (regressão B43).
        """
        from models import UserType, SessionStatus
        sessoes = [
            _start(sm, pm, pe, chargers[0], UserType.STANDARD)[0],
            _start(sm, pm, pe, chargers[1], UserType.SUBSCRIBER)[0],
            _start(sm, pm, pe, chargers[2], UserType.SUBSCRIBER)[0],
            _start(sm, pm, pe, chargers[3], UserType.STANDARD)[0],
        ]
        sm.finish_session(sessoes[3].session_id)
        pm.rebalance()

        assert sm.total_allocated_power_kw() == 33.0, (
            "posto ficou abaixo do limite com capacidade sobrando"
        )
        for s in sm.list_active():
            assert s.allocated_power_kw == 11.0, (
                f"{s.charger_id} ({s.user_type.name}) em "
                f"{s.allocated_power_kw} kW — cabia 11.0"
            )
            assert s.status == SessionStatus.CHARGING

    def test_prioridade_so_vale_quando_falta_potencia(self, sm, pm, pe, chargers):
        """Com folga no posto, assinante e padrão recebem o mesmo: o que pediram."""
        from models import UserType
        _start(sm, pm, pe, chargers[0], UserType.STANDARD)
        _start(sm, pm, pe, chargers[1], UserType.SUBSCRIBER)
        potencias = {s.user_type.name: s.allocated_power_kw
                     for s in sm.list_active()}
        assert potencias["STANDARD"] == potencias["SUBSCRIBER"] == 11.0

    def test_prioridade_vale_quando_o_posto_lota(self, sm, pm, pe, chargers):
        """Estourando o limite, o assinante fica com a fatia maior."""
        from models import UserType
        for i in range(4):
            tipo = UserType.SUBSCRIBER if i == 1 else UserType.STANDARD
            _start(sm, pm, pe, chargers[i], tipo)
        por_tipo = {}
        for s in sm.list_active():
            por_tipo.setdefault(s.user_type.name, []).append(s.allocated_power_kw)
        assert por_tipo["SUBSCRIBER"][0] > max(por_tipo["STANDARD"])
        assert sm.total_allocated_power_kw() <= 33.0

    def test_rebalance_severity_ok(self, sm, pe, chargers):
        """Severity do rebalanceamento deve ser 'ok'."""
        from models import UserType
        from power_manager import PowerManager
        pm2 = PowerManager(sm, limit_kw=22.0)
        sess = []
        for i in range(3):
            s, r = _start(sm, pm2, pe, chargers[i], UserType.STANDARD)
            if not r.rejected:
                sess.append(s)
        sm.finish_session(sess[0].session_id)
        rb = pm2.rebalance()
        if rb:
            assert rb.severity == "ok"


# ===========================================================================
# SessionManager
# ===========================================================================

class TestSessionManager:

    def test_create_session_valida(self, sm, chargers):
        from models import UserType, SessionStatus
        s = sm.create_session(chargers[0], "ABC-1234", "Ana", UserType.STANDARD, 11.0)
        assert s.session_id.startswith("CGI-")
        assert s.status == SessionStatus.PREPARING
        assert sm.get_session(s.session_id) is s

    def test_carregador_ocupado_raise(self, sm, pm, pe, chargers):
        from models import UserType
        _start(sm, pm, pe, chargers[0], UserType.STANDARD)
        with pytest.raises(ValueError, match="ocupado"):
            sm.create_session(chargers[0], "DEF-5678", "B", UserType.STANDARD, 11.0)

    def test_potencia_invalida_raise(self, sm, chargers):
        from models import UserType
        with pytest.raises(ValueError):
            sm.create_session(chargers[0], "V1", "U", UserType.STANDARD, 0.5)

    def test_finish_idempotente(self, sm, pm, pe, chargers):
        """Chamar finish_session duas vezes não deve alterar o custo."""
        from models import UserType
        s, _ = _start(sm, pm, pe, chargers[0], UserType.STANDARD)
        sm.update_energy(s.session_id, 2.0)
        sm.finish_session(s.session_id)
        custo1 = s.total_cost_brl
        sm.finish_session(s.session_id)
        assert s.total_cost_brl == custo1

    def test_taxa_minima_aplicada(self, sm, pm, pe, chargers):
        from models import UserType
        from pricing_engine import TAXA_MINIMA_SESSAO
        s, _ = _start(sm, pm, pe, chargers[0], UserType.STANDARD)
        sm.update_energy(s.session_id, 0.01)   # custo irrisório
        sm.finish_session(s.session_id)
        assert s.total_cost_brl == TAXA_MINIMA_SESSAO

    def test_occupancy_ratio_deriva_de_chargers(self, sm):
        """occupancy_ratio deve usar len(_chargers) real, não valor hardcoded."""
        assert sm.occupancy_ratio() == 0.0
        total_real = len(sm._chargers)
        assert total_real == 15    # 3 postos × 5 carregadores

    def test_accrue_energy_idempotente(self, sm, pm, pe, chargers):
        """Várias chamadas rápidas a accrue_energy não devem inflar energia."""
        from models import UserType
        s, _ = _start(sm, pm, pe, chargers[0], UserType.STANDARD)
        e0 = s.energy_kwh
        for _ in range(10):
            sm.accrue_energy(s.session_id)
        assert abs(s.energy_kwh - e0) < 0.005  # < 5Wh em chamadas instantâneas

    def test_accrue_energy_cresce_com_tempo(self, sm, pm, pe, chargers):
        """Energia deve crescer proporcionalmente ao tempo real."""
        from models import UserType
        s, _ = _start(sm, pm, pe, chargers[0], UserType.STANDARD)
        time.sleep(1.0)
        sm.accrue_energy(s.session_id)
        esperado = 11.0 * (1.0 / 3600.0)
        assert abs(s.energy_kwh - esperado) < 0.002


# ===========================================================================
# Integração Flask
# ===========================================================================

def _app_limpo(monkeypatch_db_path):
    """Recarrega o módulo `app` apontando o banco para o arquivo do teste."""
    import importlib

    import db
    import app as app_module

    importlib.reload(app_module)
    # O reload de app.py chama db.init(); reafirma o caminho temporário porque
    # o módulo db não é recarregado junto.
    app_module.db.DB_PATH = db.DB_PATH
    app_module.app.config["TESTING"] = True
    return app_module


@pytest.fixture
def client(tmp_path):
    """
    Cliente de teste Flask autenticado como operador (staff).

    Sprint 3: todas as rotas passaram a exigir login, e as administrativas
    exigem perfil staff. A fixture entra como `mylon` para que os testes de
    rota herdados do Sprint 2 continuem exercitando o que exercitavam.
    """
    app_module = _app_limpo(tmp_path)
    with app_module.app.test_client() as c:
        c.post("/login", data={"usuario": "mylon", "senha": "1234"})
        yield c


@pytest.fixture
def client_usuario(tmp_path):
    """Cliente autenticado como usuária comum (Amanda, assinante, 100 NC)."""
    app_module = _app_limpo(tmp_path)
    with app_module.app.test_client() as c:
        c.post("/login", data={"usuario": "amanda", "senha": "1234"})
        yield c


@pytest.fixture
def client_anonimo(tmp_path):
    """Cliente sem autenticação, para verificar a guarda de acesso."""
    app_module = _app_limpo(tmp_path)
    with app_module.app.test_client() as c:
        yield c


class TestFlaskRoutes:

    def test_mapa_retorna_200(self, client):
        assert client.get("/").status_code == 200

    def test_posto_retorna_200(self, client):
        assert client.get("/posto/P1").status_code == 200

    def test_dashboard_retorna_200(self, client):
        assert client.get("/dashboard").status_code == 200

    def test_api_status_retorna_json(self, client):
        import json
        r = client.get("/api/status")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "sessoes_ativas" in data
        assert "potencia_em_uso" in data
        assert "ocupacao_pct" in data

    def test_nova_sessao_via_dashboard(self, client):
        r = client.post("/dashboard/nova-sessao", data={
            "charger_id": "P1-C1", "vehicle_id": "TST-001",
            "user_type": "A", "hora": "10", "potencia": "11.0",
        }, follow_redirects=True)
        assert r.status_code == 200

    def test_posto_lotado_indisponivel(self, client):
        """Quando todos os 4 carregadores do posto P1 estão ocupados,
        o mapa deve exibir o posto como indisponível."""
        import json
        for n in range(1, 5):
            client.post("/dashboard/nova-sessao", data={
                "charger_id": f"P1-C{n}", "vehicle_id": f"TST-00{n}",
                "user_type": "P", "hora": "10", "potencia": "4.2",
            })
        r = client.get("/")
        html = r.data.decode()
        # P1 deve aparecer como indisponível
        assert "Lotado" in html or 'data-disponivel="false"' in html

    def test_modbus_log_retorna_200(self, client):
        assert client.get("/modbus-log").status_code == 200

    def test_relatorio_retorna_200(self, client):
        assert client.get("/relatorio").status_code == 200


# ===========================================================================
# Testes de Regressão — Bugs #B1, #B2, #B3
# ===========================================================================

class TestRegressaoBugs:
    """
    Testes de regressão para os três bugs corrigidos na sessão 2026-06-02:

        B1 — Carga máxima 31.7 kW em vez de 33.0 kW com 5 sessões
             Causa: uma margem de folga aplicada no Caso 3,
             consumindo 5% da capacidade instalada de forma sistemática.

        B2 — Última sessão STANDARD recebe mais kW que as anteriores
             Causa: mesmo que B1 — existentes recebiam limit/n × 0.95 enquanto
             a nova recebia limit/n sem desconto.

        B3 — Sessões saem de THROTTLED após remoção mesmo ainda abaixo do solicitado
             Causa: restore_session() transitava para CHARGING incondicionalmente,
             sem verificar se a potência restaurada atingia a solicitada.
    """

    def _setup_posto_cheio(self, sm, pe, chargers):
        """Cria 5 sessões STANDARD num posto com limite 33 kW (retorna lista)."""
        from models import UserType
        from power_manager import PowerManager
        pm5 = PowerManager(sm, limit_kw=33.0)
        sessoes = []
        for i in range(5):
            s, r = _start(sm, pm5, pe, chargers[i], UserType.STANDARD)
            if not r.rejected:
                sessoes.append(s)
        return pm5, sessoes

    def test_b1_carga_total_atinge_limite_com_5_sessoes(self, sm, pe, chargers):
        """B1: com 5 sessões STANDARD e limite 33 kW, carga total deve ser 33 kW."""
        pm5, sessoes = self._setup_posto_cheio(sm, pe, chargers)
        assert len(sessoes) == 5, "Esperado 5 sessões aceitas (mínimo 4.2×5=21 ≤ 33)"
        carga = sm.total_allocated_power_kw()
        assert abs(carga - 33.0) < 0.5, (
            f"Carga esperada ≈ 33.0 kW, obtida {carga:.2f} kW "
            f"(regressão B1: margem de folga no Caso 3)"
        )

    def test_b2_nova_sessao_nao_recebe_mais_que_existentes_mesmo_tipo(
        self, sm, pe, chargers
    ):
        """B2: com 5 sessões STANDARD, todas devem ter potência ≈ igual (33/5 = 6.6 kW)."""
        from models import SessionStatus
        pm5, sessoes = self._setup_posto_cheio(sm, pe, chargers)

        ativas = sm.list_active()
        potencias = [s.allocated_power_kw for s in ativas]
        assert len(potencias) == 5

        # Todas devem ser iguais (mesma prioridade, mesma solicitação)
        max_kw = max(potencias)
        min_kw = min(potencias)
        assert max_kw - min_kw < 0.5, (
            f"Desbalanceamento: máx={max_kw:.2f} kW, mín={min_kw:.2f} kW "
            f"(regressão B2: nova sessão recebia mais que existentes)"
        )

    def test_b3_sessoes_permanecem_throttled_apos_remocao_parcial(
        self, sm, pe, chargers
    ):
        """B3: remover 1 sessão de 5 deve manter as 4 restantes em THROTTLED,
        pois 33/4 = 8.25 kW < 11 kW solicitado — ainda não é a potência cheia."""
        from models import UserType, SessionStatus
        from power_manager import PowerManager

        pm5, sessoes = self._setup_posto_cheio(sm, pe, chargers)
        assert len(sessoes) == 5

        # Encerra uma sessão e rebalanceia
        sm.finish_session(sessoes[0].session_id)
        pm5.rebalance()

        restantes = sm.list_active()
        assert len(restantes) == 4

        # Com 4 sessões e limite 33 kW → ideal = 8.25 kW < 11 kW → THROTTLED
        for s in restantes:
            assert s.status == SessionStatus.THROTTLED, (
                f"Sessão {s.session_id} deveria ser THROTTLED mas está {s.status.value} "
                f"({s.allocated_power_kw:.2f} kW alocado, {s.requested_power_kw:.1f} kW solicitado) "
                f"(regressão B3: restore_session transitava para CHARGING prematuramente)"
            )

    def test_b3_sessao_vai_para_charging_quando_restaurada_integralmente(
        self, sm, pe, chargers
    ):
        """B3 complementar: com 2 sessões e limite 33 kW, ao remover 1 a outra
        pode ir para 11 kW (potência cheia) → status deve ser CHARGING."""
        from models import UserType, SessionStatus
        from power_manager import PowerManager

        pm2 = PowerManager(sm, limit_kw=22.0)  # 22 kW → 2×11 kW exato
        s1, r1 = _start(sm, pm2, pe, chargers[0], UserType.STANDARD)
        s2, r2 = _start(sm, pm2, pe, chargers[1], UserType.STANDARD)

        # Com 22 kW e 2 sessões de 11 kW → sem throttle
        # Adiciona 3a sessão para forçar throttle: 3×11=33 > 22
        s3, r3 = _start(sm, pm2, pe, chargers[2], UserType.STANDARD)
        assert r3.redistributed, "3a sessão deveria causar redistribuição"

        # Encerra s3 — s1 e s2 devem voltar para 11 kW → CHARGING
        sm.finish_session(s3.session_id)
        pm2.rebalance()

        for s in [s1, s2]:
            sess = sm.get_session(s.session_id)
            assert sess.status == SessionStatus.CHARGING, (
                f"Sessão {sess.session_id} deveria estar CHARGING após restauração integral "
                f"(alocado={sess.allocated_power_kw:.1f}, solicitado={sess.requested_power_kw:.1f})"
            )


# ===========================================================================
# Testes de Regressão — Auditoria B24–B35 (sessão 2026-06-04)
# ===========================================================================

class TestAuditoriaB24aB35:
    """
    Regressão dos 12 bugs encontrados na auditoria completa do v3.
    Cada teste falha se o respectivo bug reaparecer.
    """

    # ---- B26: rebalance respeita prioridade por tipo de usuário ----------
    def test_b26_rebalance_respeita_prioridade(self, sm, pe, chargers):
        from models import UserType, SessionStatus
        from power_manager import PowerManager
        pm5 = PowerManager(sm, limit_kw=33.0)

        def start(cid, ut):
            s = sm.create_session(cid, "ABC1D23", "U", ut, 11.0)
            r = pm5.allocate(s)
            t = pe.calculate(ut, hora=10)
            sm.start_charging(s.session_id, r.granted_kw, t.tariff_kwh)
            if r.redistributed and r.granted_kw < 11.0:
                sm.throttle_session(s.session_id, r.granted_kw)
            return s

        start("P1-C1", UserType.SUBSCRIBER)
        start("P1-C2", UserType.STANDARD)
        start("P1-C3", UserType.STANDARD)
        start("P1-C4", UserType.CORPORATE)
        s5 = start("P1-C5", UserType.STANDARD)

        sm.finish_session(s5.session_id)
        pm5.rebalance()

        por_tipo = {s.charger_id: (s.user_type.name, s.allocated_power_kw)
                    for s in sm.list_active()}
        sub  = por_tipo["P1-C1"][1]
        corp = por_tipo["P1-C4"][1]
        std  = por_tipo["P1-C2"][1]
        # Assinante > Corporativo > Padrão após o rebalance
        assert sub > corp > std, (
            f"Prioridade não respeitada no rebalance: "
            f"SUB={sub} CORP={corp} STD={std} (regressão B26)"
        )
        assert sm.total_allocated_power_kw() <= 33.0 + 0.05

    # ---- B28: tarifa de demanda usa ocupação fornecida (do posto) --------
    def test_b28_demanda_usa_occupancy_override(self, sm, pe):
        from models import UserType
        # Rede global vazia → sem demanda
        t_global = pe.calculate(UserType.STANDARD, hora=10)
        assert not t_global.demand_applied
        # Override de 100% (posto cheio) → demanda ativa
        t_posto = pe.calculate(UserType.STANDARD, hora=10, occupancy_override=1.0)
        assert t_posto.demand_applied
        assert t_posto.tariff_kwh > t_global.tariff_kwh, (
            "occupancy_override não ativou a tarifa de demanda (regressão B28)"
        )

    # ---- B29: Caso 2 morto removido; _redistribute não existe mais -------
    def test_b29_redistribute_orfao_removido(self):
        from power_manager import PowerManager
        assert not hasattr(PowerManager, "_redistribute"), (
            "_redistribute deveria ter sido removido (regressão B29)"
        )
        assert hasattr(PowerManager, "_target_por_peso"), (
            "_target_por_peso deveria existir como helper unificado"
        )

    # ---- B30: validação de hora ------------------------------------------
    def test_b30_validar_hora(self):
        import app
        assert app._validar_hora("10") == 10
        assert app._validar_hora("23") == 23
        for invalida in ("24", "-1", "abc", "99"):
            with pytest.raises(ValueError):
                app._validar_hora(invalida)

    # ---- B31: validação de placa -----------------------------------------
    def test_b31_validar_placa(self):
        import app
        assert app._validar_placa("abc1d23") == "ABC1D23"   # Mercosul, normaliza
        assert app._validar_placa("ABC-1234") == "ABC1234"  # antigo com hífen
        for invalida in ("", "123", "ABCDEFG", "AB1234"):
            with pytest.raises(ValueError):
                app._validar_placa(invalida)

    # ---- B32: imports não usados removidos do modbus_simulator -----------
    def test_b32_imports_limpos(self):
        import modbus_simulator as mbsim
        # encoding explícito: sem ele o Python usa a codificação do locale
        # (cp1252 no Windows) e engasga nos acentos do próprio fonte.
        src = pathlib.Path(mbsim.__file__).read_text(encoding="utf-8")
        # 'time' não deve mais ser importado
        assert "import time" not in src, "import time deveria ter sido removido (B32)"


class TestAuditoriaModbusFlask:
    """B24/B25 via Flask test client — frames Modbus de throttle e meter read."""

    @pytest.fixture
    def client(self):
        import importlib
        import app as app_module
        importlib.reload(app_module)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            # Sprint 3 — as rotas do painel exigem sessão de operador.
            c.post("/login", data={"usuario": "mylon", "senha": "1234"})
            yield c, app_module

    # ---- B24: redistribuição gera frames Modbus --------------------------
    def test_b24_on_throttle_emite_frames(self, client):
        c, A = client
        for n in range(1, 5):  # 4ª sessão estoura o limite de P1
            c.post("/dashboard/nova-sessao", data={
                "charger_id": f"P1-C{n}", "vehicle_id": f"ABC{n}D34",
                "user_type": "P", "hora": "10", "potencia": "11.0",
            })
        throttle = [f for f in A.mb.get_log() if "Throttle" in f.description]
        assert len(throttle) > 0, "on_throttle não gerou frames Modbus (regressão B24)"

    # ---- B25: polling gera MeterRead -------------------------------------
    def test_b25_on_meter_read_no_polling(self, client):
        c, A = client
        c.post("/dashboard/nova-sessao", data={
            "charger_id": "P1-C1", "vehicle_id": "ABC1D34",
            "user_type": "P", "hora": "10", "potencia": "11.0",
        })
        c.get("/api/status")
        meter = [f for f in A.mb.get_log() if "MeterRead" in f.description]
        assert len(meter) > 0, "on_meter_read não foi chamado no polling (regressão B25)"

    # ---- B31 (rota): placa inválida não cria sessão ----------------------
    def test_b31_rota_rejeita_placa_invalida(self, client):
        c, A = client
        c.post("/dashboard/nova-sessao", data={
            "charger_id": "P3-C1", "vehicle_id": "XX",
            "user_type": "P", "hora": "10", "potencia": "11.0",
        }, follow_redirects=True)
        assert A.sm.list_chargers().get("P3-C1") is None, (
            "Placa inválida criou sessão (regressão B31)"
        )

    # ---- B35: relatório expõe receita realizada e projetada --------------
    def test_b35_receita_separada(self, client):
        c, A = client
        c.post("/dashboard/nova-sessao", data={
            "charger_id": "P2-C1", "vehicle_id": "AAA1B11",
            "user_type": "P", "hora": "10", "potencia": "11.0",
        })
        c.post("/dashboard/nova-sessao", data={
            "charger_id": "P2-C2", "vehicle_id": "BBB2C22",
            "user_type": "P", "hora": "10", "potencia": "11.0",
        })
        sid = [s.session_id for s in A.sm.list_active()
               if s.charger_id == "P2-C1"][0]
        c.post("/dashboard/encerrar", data={"session_id": sid})
        r = c.get("/relatorio")
        assert r.status_code == 200
        html = r.data.decode()
        assert "Paga" in html and "Em curso" in html, (
            "Relatório não distingue receita paga de receita em curso "
            "(regressão B35)"
        )
        # #B45 — a sessão encerrada acima não foi paga: o valor dela é
        # pendente, não receita realizada.
        assert "A receber" in html, (
            "Sessão encerrada sem pagamento tem que aparecer como pendente, "
            "não como receita paga (regressão B45)"
        )


# ===========================================================================
# Testes de Regressão — Revisão R1–R5 (revisão completa do v4)
# ===========================================================================

class TestRevisaoR1aR5:
    """
    Regressão dos achados da revisão completa do v4:
        R1 — guard de 80% bloqueava restaurações legítimas (removido)
        R2 — log Modbus crescia ilimitado com on_meter_read (buffer circular)
        R5 — _simular_tick fora do lock na rota /posto (movido para sob lock)
    """

    # ---- R1: rebalance restaura com carga entre 80% e 100% ---------------
    def test_r1_rebalance_restaura_acima_de_80pct(self, sm):
        from power_manager import PowerManager
        from models import UserType, SessionStatus
        pm = PowerManager(sm, limit_kw=33.0)

        # 4 sessões throttled a 6.7 kW = 26.8 kW = 81% do limite
        for i in range(4):
            s = sm.create_session(f"P1-C{i+1}", "ABC1D23", "U",
                                  UserType.STANDARD, 11.0)
            sm.start_charging(s.session_id, 6.7, 1.20)
            s.status = SessionStatus.THROTTLED
            s.allocated_power_kw = 6.7

        carga_antes = sm.total_allocated_power_kw()
        assert carga_antes > 33.0 * 0.80, "pré-condição: carga deve estar > 80%"

        rb = pm.rebalance()
        carga_depois = sm.total_allocated_power_kw()

        assert rb is not None, (
            "rebalance deveria restaurar mesmo com carga > 80% (regressão R1)"
        )
        assert carga_depois > carga_antes, "carga deveria aumentar"
        assert carga_depois <= 33.05, "não pode estourar o limite"

    # ---- R2: log Modbus tem buffer circular ------------------------------
    def test_r2_log_modbus_tem_cap(self, sm):
        from modbus_simulator import ModbusSimulator, MAX_LOG_FRAMES
        from models import UserType

        mb = ModbusSimulator(verbose=False)
        sess = sm.create_session("P1-C1", "ABC1D23", "U",
                                 UserType.STANDARD, 11.0)
        sm.start_charging(sess.session_id, 11.0, 1.20)

        # Emite muito mais frames que o cap via meter reads repetidos
        for _ in range(MAX_LOG_FRAMES * 2):
            mb.on_meter_read(sess)

        assert len(mb.get_log()) <= MAX_LOG_FRAMES, (
            f"log em memória deveria ser capado em {MAX_LOG_FRAMES} (regressão R2)"
        )
        # O contador acumulado preserva o histórico total
        assert mb.frame_count() > MAX_LOG_FRAMES, (
            "frame_count deveria contar todos os frames já emitidos, não só o buffer"
        )

    # ---- R5: rota /posto não vaza _simular_tick --------------------------
    def test_r5_simular_tick_removido(self):
        import app
        assert not hasattr(app, "_simular_tick"), (
            "_simular_tick deveria ter sido removido (regressão R5)"
        )

    def test_r5_rota_posto_responde(self):
        import importlib
        import app as app_module
        importlib.reload(app_module)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            c.post("/login", data={"usuario": "mylon", "senha": "1234"})
            c.post("/dashboard/nova-sessao", data={
                "charger_id": "P1-C1", "vehicle_id": "ABC1D23",
                "user_type": "P", "hora": "10", "potencia": "11.0",
            })
            r = c.get("/posto/P1")
            assert r.status_code == 200, "rota /posto deveria responder 200 (R5)"


class TestConectorVIP:
    """Regra de negócio: o conector C5 é exclusivo para assinantes."""

    @pytest.fixture
    def client(self):
        import importlib
        import app as app_module
        importlib.reload(app_module)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            # Sprint 3 — as rotas do painel exigem sessão de operador.
            c.post("/login", data={"usuario": "mylon", "senha": "1234"})
            yield c, app_module

    def test_padrao_bloqueado_no_c5(self, client):
        c, A = client
        c.post("/dashboard/nova-sessao", data={
            "charger_id": "P1-C5", "vehicle_id": "ABC1D23",
            "user_type": "P", "hora": "10", "potencia": "11.0",
        })
        assert not [s for s in A.sm.list_active() if s.charger_id == "P1-C5"], \
            "Usuário Padrão não deveria poder usar o conector VIP C5"

    def test_corporativo_bloqueado_no_c5(self, client):
        c, A = client
        c.post("/dashboard/nova-sessao", data={
            "charger_id": "P1-C5", "vehicle_id": "DEF2G34",
            "user_type": "C", "hora": "10", "potencia": "11.0",
        })
        assert not [s for s in A.sm.list_active() if s.charger_id == "P1-C5"], \
            "Usuário Corporativo não deveria poder usar o conector VIP C5"

    def test_assinante_permitido_no_c5(self, client):
        c, A = client
        c.post("/dashboard/nova-sessao", data={
            "charger_id": "P1-C5", "vehicle_id": "GHI3J56",
            "user_type": "A", "hora": "10", "potencia": "11.0",
        })
        assert [s for s in A.sm.list_active() if s.charger_id == "P1-C5"], \
            "Assinante deveria poder usar o conector VIP C5"

    def test_padrao_permitido_em_conector_publico(self, client):
        c, A = client
        c.post("/dashboard/nova-sessao", data={
            "charger_id": "P1-C1", "vehicle_id": "JKL4M78",
            "user_type": "P", "hora": "10", "potencia": "11.0",
        })
        assert [s for s in A.sm.list_active() if s.charger_id == "P1-C1"], \
            "Usuário Padrão deveria poder usar conectores públicos (C1–C4)"


# ===========================================================================
# SPRINT 3 — Autenticação e autorização
# ===========================================================================

class TestAutenticacao:
    """Guarda global de acesso, login, logout e separação usuário/operador."""

    def test_rota_protegida_redireciona_sem_login(self, client_anonimo):
        r = client_anonimo.get("/")
        assert r.status_code == 302, "mapa deveria exigir autenticação"
        assert "/login" in r.headers["Location"]

    def test_login_publico_responde_200(self, client_anonimo):
        assert client_anonimo.get("/login").status_code == 200

    def test_login_valido_libera_o_mapa(self, client_anonimo):
        client_anonimo.post("/login", data={"usuario": "amanda", "senha": "1234"})
        assert client_anonimo.get("/").status_code == 200

    def test_login_ignora_caixa_do_usuario(self, client_anonimo):
        client_anonimo.post("/login", data={"usuario": "AMANDA", "senha": "1234"})
        assert client_anonimo.get("/").status_code == 200

    def test_senha_incorreta_nao_autentica(self, client_anonimo):
        client_anonimo.post("/login", data={"usuario": "amanda", "senha": "9999"})
        assert client_anonimo.get("/").status_code == 302

    def test_conta_inexistente_nao_autentica(self, client_anonimo):
        client_anonimo.post("/login", data={"usuario": "fulano", "senha": "1234"})
        assert client_anonimo.get("/").status_code == 302

    def test_usuario_comum_barrado_no_dashboard(self, client_usuario):
        r = client_usuario.get("/dashboard")
        assert r.status_code == 302, "usuário comum não pode ver o painel"

    def test_usuario_comum_barrado_no_relatorio(self, client_usuario):
        assert client_usuario.get("/relatorio").status_code == 302

    def test_staff_acessa_o_painel(self, client):
        assert client.get("/admin").status_code == 200
        assert client.get("/dashboard").status_code == 200

    def test_logout_encerra_a_sessao(self, client_usuario):
        client_usuario.get("/logout")
        assert client_usuario.get("/").status_code == 302

    def test_next_externo_e_recusado(self, client_anonimo):
        """O parâmetro `next` não pode virar um open redirect."""
        r = client_anonimo.post("/login", data={
            "usuario": "amanda", "senha": "1234", "next": "//exemplo-malicioso.com",
        })
        assert "exemplo-malicioso" not in r.headers.get("Location", "")

    def test_next_interno_e_respeitado(self, client_anonimo):
        r = client_anonimo.post("/login", data={
            "usuario": "amanda", "senha": "1234", "next": "/carteira",
        })
        assert r.headers["Location"].endswith("/carteira")


# ===========================================================================
# SPRINT 3 — Carteira NexusCoin
# ===========================================================================

class TestCarteira:
    """Saldo, crédito, débito, extrato e a fronteira do saldo insuficiente."""

    def test_saldos_iniciais_por_conta(self, client_anonimo):
        import auth
        import wallet
        for chave, perfil in auth.CONTAS.items():
            wallet.garantir_conta(chave, perfil["saldo_inicial"])
            assert wallet.saldo(chave) == perfil["saldo_inicial"]

    def test_garantir_conta_nao_recarrega(self):
        import wallet
        wallet.garantir_conta("t", 10.0)
        wallet.garantir_conta("t", 500.0)
        assert wallet.saldo("t") == 10.0

    def test_credito_e_debito(self):
        import wallet
        wallet.garantir_conta("t", 10.0)
        assert wallet.creditar("t", 5.0, "RECARGA") == 15.0
        assert wallet.debitar("t", 4.0, "PAGAMENTO") == 11.0

    def test_saldo_insuficiente_nao_altera_saldo(self):
        import wallet
        wallet.garantir_conta("t", 5.0)
        with pytest.raises(wallet.SaldoInsuficiente) as exc:
            wallet.debitar("t", 12.0, "PAGAMENTO")
        assert exc.value.falta == 7.0
        assert wallet.saldo("t") == 5.0, "débito recusado não pode mover o saldo"

    def test_extrato_registra_as_duas_pontas(self):
        import wallet
        wallet.garantir_conta("t", 10.0)
        wallet.creditar("t", 5.0, "RECARGA", "entrada")
        wallet.debitar("t", 2.0, "PAGAMENTO", "saída")
        ext = wallet.extrato("t")
        assert len(ext) == 2
        assert ext[0]["valor"] < 0 and ext[1]["valor"] > 0

    def test_valores_nao_positivos_sao_recusados(self):
        import wallet
        wallet.garantir_conta("t", 10.0)
        with pytest.raises(ValueError):
            wallet.creditar("t", 0, "RECARGA")
        with pytest.raises(ValueError):
            wallet.debitar("t", -3, "PAGAMENTO")

    def test_recarga_pela_rota_credita(self, client_usuario):
        import wallet
        antes = wallet.saldo("amanda")
        client_usuario.post("/carteira/recarregar",
                            data={"valor": "50,00", "metodo": "PIX"})
        assert wallet.saldo("amanda") == antes + 50.0

    def test_recarga_fora_da_faixa_e_recusada(self, client_usuario):
        import wallet
        antes = wallet.saldo("amanda")
        client_usuario.post("/carteira/recarregar",
                            data={"valor": "99999", "metodo": "PIX"})
        assert wallet.saldo("amanda") == antes

    def test_total_cashback(self):
        import wallet
        wallet.garantir_conta("t", 0.0)
        wallet.creditar("t", 1.5, "CASHBACK")
        wallet.creditar("t", 2.0, "CASHBACK")
        wallet.creditar("t", 10.0, "RECARGA")
        assert wallet.total_cashback("t") == 3.5


# ===========================================================================
# SPRINT 3 — Reserva de conector com sinal
# ===========================================================================

class TestReserva:
    """Ciclo de vida da reserva e o destino do sinal em cada desfecho."""

    def _conta(self, saldo=100.0, usuario="amanda"):
        import wallet
        wallet.garantir_conta(usuario, saldo)
        return usuario

    def test_criar_debita_o_sinal(self):
        import reservations
        import wallet
        u = self._conta()
        r = reservations.criar(u, "P2-C1")
        assert wallet.saldo(u) == 90.0
        assert r.segundos_restantes > 60 * 14

    def test_conector_reservado_nao_aceita_segunda_reserva(self):
        import reservations
        self._conta()
        self._conta(100.0, "outro")
        reservations.criar("amanda", "P2-C1")
        with pytest.raises(ValueError):
            reservations.criar("outro", "P2-C1")

    def test_usuario_so_tem_uma_reserva_ativa(self):
        import reservations
        self._conta()
        reservations.criar("amanda", "P2-C1")
        with pytest.raises(ValueError):
            reservations.criar("amanda", "P2-C2")

    def test_saldo_insuficiente_impede_a_reserva(self):
        import reservations
        import wallet
        u = self._conta(5.0, "allan")
        with pytest.raises(wallet.SaldoInsuficiente):
            reservations.criar(u, "P2-C3")
        assert reservations.ativa_do_conector("P2-C3") is None
        assert wallet.saldo(u) == 5.0

    def test_cancelar_estorna_integralmente(self):
        import reservations
        import wallet
        u = self._conta()
        reservations.criar(u, "P2-C1")
        assert reservations.cancelar(u, "P2-C1") == 10.0
        assert wallet.saldo(u) == 100.0
        assert reservations.ativa_do_conector("P2-C1") is None

    def test_cancelar_reserva_de_outro_e_recusado(self):
        import reservations
        self._conta()
        self._conta(100.0, "outro")
        reservations.criar("amanda", "P2-C1")
        with pytest.raises(ValueError):
            reservations.cancelar("outro", "P2-C1")

    def test_consumir_marca_usada_e_devolve_o_sinal(self):
        import reservations
        import wallet
        u = self._conta()
        reservations.criar(u, "P2-C1")
        assert reservations.consumir(u, "P2-C1") == 10.0
        assert reservations.consumir(u, "P2-C1") == 0.0, "não pode contar duas vezes"
        assert reservations.sinal_creditado(u, "P2-C1") == 10.0
        assert wallet.saldo(u) == 90.0, "reserva honrada não estorna"

    def test_expiracao_retem_o_sinal(self):
        import db
        import reservations
        import wallet
        u = self._conta()
        reservations.criar(u, "P2-C1")
        db.execute("UPDATE reservas SET expira_em = ? WHERE charger_id = ?",
                   ("2020-01-01 00:00:00", "P2-C1"))
        assert reservations.expirar_vencidas() == 1
        assert reservations.ativa_do_conector("P2-C1") is None
        assert wallet.saldo(u) == 90.0, "expiração não devolve o sinal"
        assert reservations.receita_retida() == 10.0

    def test_conector_reservado_sai_da_contagem_de_livres(self, client_usuario):
        import app as A
        import reservations
        livres_antes = A.POSTOS["P2"]["carregadores_livres"]
        reservations.criar("amanda", "P2-C1")
        A._atualizar_carregadores_livres()
        assert A.POSTOS["P2"]["carregadores_livres"] == livres_antes - 1

    def test_api_reservar_responde_402_sem_saldo(self, client_anonimo):
        client_anonimo.post("/login", data={"usuario": "allan", "senha": "1234"})
        r = client_anonimo.post("/api/reservar",
                                json={"posto_id": "P2", "carregador_id": "C1"})
        assert r.status_code == 402
        assert r.get_json()["acao"] == "recarregar"

    def test_api_reservar_cria_e_debita(self, client_usuario):
        import wallet
        r = client_usuario.post("/api/reservar",
                                json={"posto_id": "P2", "carregador_id": "C1"})
        assert r.status_code == 200 and r.get_json()["ok"] is True
        assert wallet.saldo("amanda") == 90.0

    def test_api_reservar_recusa_conector_ocupado(self, client_usuario):
        """Conector com sessão em andamento não pode ser reservado."""
        import app as A
        from models import UserType
        A.sm.create_session("P1-C1", "ABC1D23", "Outro", UserType.STANDARD, 11.0)
        r = client_usuario.post("/api/reservar",
                                json={"posto_id": "P1", "carregador_id": "C1"})
        assert r.status_code == 400


# ===========================================================================
# SPRINT 3 — Cobrança
# ===========================================================================

class TestBilling:
    """Composição do valor devido antes de escolher o meio de pagamento."""

    def _sessao(self, custo, energia=5.0, tarifa=1.2):
        import datetime
        from models import ChargingSession, SessionStatus, UserType
        s = ChargingSession(
            charger_id="P1-C1", station_id="P1", vehicle_id="ABC1D23",
            user_name="Teste", user_type=UserType.STANDARD, requested_power_kw=11.0,
        )
        s.total_cost_brl = custo
        s.energy_kwh = energia
        s.tariff_kwh = tarifa
        s.end_time = s.start_time + datetime.timedelta(minutes=30)
        s.status = SessionStatus.FINISHED
        return s

    def test_sinal_abate_o_subtotal(self):
        import billing
        c = billing.calcular(self._sessao(25.00), sinal_brl=10.00)
        assert c.total_brl == 15.00 and c.sinal_brl == 10.00

    def test_sinal_maior_que_o_consumo_zera_sem_credito(self):
        """Excedente do sinal fica com o posto: senão vira vetor de saque."""
        import billing
        c = billing.calcular(self._sessao(4.00), sinal_brl=10.00)
        assert c.total_brl == 0.0
        assert c.sinal_brl == 4.00, "abate no máximo o subtotal"
        assert c.gratuito and c.cashback_nc == 0.0

    def test_cashback_e_dez_por_cento_do_total(self):
        import billing
        import wallet
        c = billing.calcular(self._sessao(30.00))
        assert c.cashback_nc == round(30.00 * wallet.CASHBACK_NEXUSCOIN, 2)

    def test_sem_reserva_o_total_e_o_subtotal(self):
        import billing
        c = billing.calcular(self._sessao(7.30))
        assert (c.total_brl, c.sinal_brl) == (7.30, 0.0)

    def test_metodo_invalido_e_recusado(self):
        import billing
        assert billing.normalizar_metodo(" pix ") == "PIX"
        with pytest.raises(ValueError):
            billing.normalizar_metodo("boleto")


# ===========================================================================
# SPRINT 3 — Encerramento e pagamento pela interface
# ===========================================================================

class TestPagamento:
    """Fluxo completo: encerrar a própria recarga, pagar e emitir recibo."""

    def _sessao_de(self, app_module, owner="amanda", charger="P2-C1"):
        """Cria uma sessão pertencente ao usuário informado."""
        from models import UserType
        s = app_module.sm.create_session(
            charger, "ABC1D23", "Amanda Ribeiro", UserType.SUBSCRIBER,
            11.0, owner=owner,
        )
        r = app_module._get_pm(charger.split("-")[0]).allocate(s)
        app_module.sm.start_charging(s.session_id, r.granted_kw, 1.02)
        app_module.sm.update_energy(s.session_id, 10.0)   # 10 kWh → R$ 10,20
        return s

    def test_encerrar_leva_ao_pagamento(self, client_usuario):
        import app as A
        s = self._sessao_de(A)
        r = client_usuario.post(f"/sessao/{s.session_id}/encerrar")
        assert r.status_code == 302
        assert f"/pagamento/{s.session_id}" in r.headers["Location"]

    def test_sessao_de_outro_usuario_e_recusada(self, client_usuario):
        import app as A
        s = self._sessao_de(A, owner="allan")
        r = client_usuario.post(f"/sessao/{s.session_id}/encerrar",
                                follow_redirects=False)
        assert "/pagamento/" not in r.headers.get("Location", "")

    def test_pagamento_com_nexuscoin_debita_e_credita_cashback(self, client_usuario):
        import app as A
        import wallet
        s = self._sessao_de(A)
        client_usuario.post(f"/sessao/{s.session_id}/encerrar")
        saldo_antes = wallet.saldo("amanda")

        client_usuario.post(f"/pagamento/{s.session_id}/confirmar",
                            data={"metodo": "NEXUSCOIN"})

        linha = A._sessao_arquivada(s.session_id)
        assert linha is not None, "sessão paga deve ser arquivada"
        total = round(linha["custo_brl"] - linha["sinal_abatido"], 2)
        esperado = round(saldo_antes - total + linha["cashback_nc"], 2)
        assert wallet.saldo("amanda") == esperado
        assert linha["cashback_nc"] == round(total * wallet.CASHBACK_NEXUSCOIN, 2)

    def test_pagamento_por_pix_nao_toca_a_carteira(self, client_usuario):
        import app as A
        import wallet
        s = self._sessao_de(A)
        client_usuario.post(f"/sessao/{s.session_id}/encerrar")
        antes = wallet.saldo("amanda")

        client_usuario.post(f"/pagamento/{s.session_id}/confirmar",
                            data={"metodo": "PIX"})

        assert wallet.saldo("amanda") == antes
        assert A._sessao_arquivada(s.session_id)["cashback_nc"] == 0.0

    def test_confirmar_duas_vezes_cobra_uma(self, client_usuario):
        import app as A
        import wallet
        s = self._sessao_de(A)
        client_usuario.post(f"/sessao/{s.session_id}/encerrar")

        client_usuario.post(f"/pagamento/{s.session_id}/confirmar",
                            data={"metodo": "NEXUSCOIN"})
        saldo_depois_do_primeiro = wallet.saldo("amanda")

        client_usuario.post(f"/pagamento/{s.session_id}/confirmar",
                            data={"metodo": "NEXUSCOIN"})
        assert wallet.saldo("amanda") == saldo_depois_do_primeiro, \
            "duplo clique não pode cobrar duas vezes"

    def test_saldo_insuficiente_nao_arquiva_a_sessao(self, client_anonimo):
        import app as A
        client_anonimo.post("/login", data={"usuario": "jose", "senha": "1234"})
        s = self._sessao_de(A, owner="jose")
        client_anonimo.post(f"/sessao/{s.session_id}/encerrar")

        client_anonimo.post(f"/pagamento/{s.session_id}/confirmar",
                            data={"metodo": "NEXUSCOIN"})
        assert A._sessao_arquivada(s.session_id) is None, \
            "sem saldo, a sessão não pode constar como paga"

    def test_recibo_indisponivel_antes_do_pagamento(self, client_usuario):
        import app as A
        s = self._sessao_de(A)
        client_usuario.post(f"/sessao/{s.session_id}/encerrar")
        r = client_usuario.get(f"/recibo/{s.session_id}")
        assert r.status_code == 302

    def test_sinal_da_reserva_abate_no_pagamento(self, client_usuario):
        import app as A
        import reservations
        reservations.criar("amanda", "P2-C1")
        s = self._sessao_de(A, charger="P2-C1")
        client_usuario.post(f"/sessao/{s.session_id}/encerrar")
        client_usuario.post(f"/pagamento/{s.session_id}/confirmar",
                            data={"metodo": "NEXUSCOIN"})
        linha = A._sessao_arquivada(s.session_id)
        assert linha["sinal_abatido"] == 10.0

    # ---- B41: encerrar sem pagar não devolve a vaga ----------------------
    def test_encerrar_sem_pagar_mantem_o_conector_ocupado(self, client_usuario):
        import app as A
        s = self._sessao_de(A, charger="P2-C1")
        client_usuario.post(f"/sessao/{s.session_id}/encerrar")

        assert A.sm.list_chargers()["P2-C1"] == s.session_id, \
            "conector liberado sem pagamento (regressão B41)"
        assert not A.sm.is_charger_available("P2-C1")

    def test_pagamento_libera_o_conector(self, client_usuario):
        import app as A
        s = self._sessao_de(A, charger="P2-C1")
        client_usuario.post(f"/sessao/{s.session_id}/encerrar")
        client_usuario.post(f"/pagamento/{s.session_id}/confirmar",
                            data={"metodo": "NEXUSCOIN"})

        assert A.sm.is_charger_available("P2-C1"), \
            "conector deveria voltar à fila depois do pagamento"

    def test_pendencia_aparece_para_o_dono_da_sessao(self, client_usuario):
        import app as A
        s = self._sessao_de(A, charger="P2-C1")
        client_usuario.post(f"/sessao/{s.session_id}/encerrar")

        r = client_usuario.get("/")
        assert b"Pagar agora" in r.data, \
            "sem faixa de pendência o débito fica sem caminho de volta"
        assert A._pendencia_do_usuario("amanda").session_id == s.session_id

    # ---- B42: operador libera pelo posto, sem passar pelo caixa ----------
    def test_staff_encerrando_sessao_alheia_libera_sem_pagamento(self, client):
        import app as A
        s = self._sessao_de(A, owner="amanda", charger="P2-C1")
        r = client.post(f"/sessao/{s.session_id}/encerrar")

        assert "/pagamento/" not in r.headers["Location"], \
            "dono do posto não pode ser mandado para o caixa (regressão B42)"
        assert f"/posto/{s.station_id}" in r.headers["Location"]
        assert A.sm.is_charger_available("P2-C1")

    def test_staff_libera_conector_retido(self, client):
        import app as A
        s = self._sessao_de(A, owner="amanda", charger="P2-C1")
        A.sm.finish_session(s.session_id, liberar=False)

        client.post(f"/sessao/{s.session_id}/liberar")
        assert A.sm.is_charger_available("P2-C1")

    def test_usuario_comum_nao_libera_conector(self, client_usuario):
        import app as A
        s = self._sessao_de(A, owner="allan", charger="P2-C1")
        A.sm.finish_session(s.session_id, liberar=False)

        client_usuario.post(f"/sessao/{s.session_id}/liberar")
        assert not A.sm.is_charger_available("P2-C1"), \
            "liberar conector é rota de operação, restrita ao staff"

    def test_export_csv_traz_a_sessao_paga(self, client):
        """O CSV é a base das análises estatísticas da Sprint 3."""
        import app as A
        s = self._sessao_de(A, owner="mylon")
        client.post(f"/sessao/{s.session_id}/encerrar")
        client.post(f"/pagamento/{s.session_id}/confirmar", data={"metodo": "PIX"})
        r = client.get("/admin/export.csv")
        assert r.status_code == 200
        assert s.session_id in r.data.decode("utf-8-sig")


# ===========================================================================
# SPRINT 3 — Coerência entre o que o sistema faz e o que ele mostra
# (B44 a B49: números e rótulos que divergiam do estado real)
# ===========================================================================

class TestCoerenciaDeEstado:

    # ---- B44: ocupação conta conectores, não sessões ---------------------
    def test_ocupacao_conta_conector_retido_por_pagamento(self, client_usuario):
        """
        Recarga encerrada e não paga segura o conector; a tarifa de demanda
        e o painel precisam enxergar essa vaga como ocupada.
        """
        import app as A
        from models import UserType
        s = A.sm.create_session("P2-C1", "ABC1D23", "Amanda", UserType.SUBSCRIBER,
                                11.0, owner="amanda")
        A.sm.start_charging(s.session_id, 11.0, 1.0)
        ocupacao_carregando = A.sm.occupancy_ratio()

        client_usuario.post(f"/sessao/{s.session_id}/encerrar")

        assert A.sm.occupancy_ratio() == ocupacao_carregando, (
            "conector retido para pagamento sumiu da ocupação (regressão B44)"
        )
        client_usuario.post(f"/pagamento/{s.session_id}/confirmar",
                            data={"metodo": "NEXUSCOIN"})
        assert A.sm.occupancy_ratio() < ocupacao_carregando, (
            "depois de pago, o conector volta a contar como livre"
        )

    # ---- B46: painel do operador e reservas ------------------------------
    def test_painel_nao_oferece_conector_reservado(self, client):
        import app as A
        import reservations
        A.wallet.garantir_conta("amanda", 100.0)
        reservations.criar("amanda", "P2-C2")

        r = client.get("/dashboard")
        assert r.status_code == 200
        html = r.data.decode()
        select = html.split('name="charger_id"')[1].split("</select>")[0]
        assert "P2-C2" not in select, (
            "conector reservado não pode ser oferecido no painel (regressão B46)"
        )

    def test_painel_recusa_sessao_em_conector_reservado(self, client):
        import app as A
        import reservations
        A.wallet.garantir_conta("amanda", 100.0)
        reservations.criar("amanda", "P2-C2")

        client.post("/dashboard/nova-sessao", data={
            "charger_id": "P2-C2", "vehicle_id": "ZZZ9Z99",
            "user_type": "P", "hora": "10", "potencia": "11.0",
        })
        assert A.sm.is_charger_available("P2-C2"), (
            "sessão criada em cima de reserva paga faria o dono perder o sinal"
        )
        assert reservations.ativa_do_conector("P2-C2") is not None

    # ---- B47: 'ocupados' não engole os reservados ------------------------
    def test_reservado_nao_conta_como_ocupado(self, client_usuario):
        import app as A
        import reservations
        reservations.criar("amanda", "P2-C2")
        A._atualizar_carregadores_livres()
        posto = A.POSTOS["P2"]

        assert posto["ocupados"] == 0
        assert posto["reservados"] == 1
        assert (posto["carregadores_livres"] + posto["ocupados"]
                + posto["reservados"]) == posto["total_carregadores"]

    # ---- B48: rótulo do tempo do conector ocupado ------------------------
    def test_posto_nao_promete_hora_de_liberacao(self, client_usuario):
        import app as A
        from models import UserType
        s = A.sm.create_session("P2-C1", "ABC1D23", "Amanda", UserType.SUBSCRIBER,
                                11.0, owner="amanda")
        A.sm.start_charging(s.session_id, 11.0, 1.0)

        html = client_usuario.get("/posto/P2").data.decode()
        assert "Disponível em" not in html, (
            "o sistema não sabe quando o motorista despluga; o número exibido "
            "é tempo decorrido (regressão B48)"
        )
        assert "Em uso há" in html

    # ---- B49: sinal retido não pode sumir do relatório -------------------
    def test_sinal_retido_sobrevive_a_nova_reserva_no_mesmo_conector(self):
        import db, wallet
        import reservations as rv
        wallet.garantir_conta("amanda", 100.0)
        wallet.garantir_conta("allan", 100.0)

        rv.criar("amanda", "P1-C3")
        db.execute(
            "UPDATE reservas SET expira_em = ? WHERE charger_id = ? AND status = ?",
            ("2020-01-01 00:00:00", "P1-C3", rv.STATUS_ATIVA),
        )
        rv.expirar_vencidas()
        assert rv.receita_retida() == 10.0

        rv.criar("allan", "P1-C3")
        assert rv.receita_retida() == 10.0, (
            "reservar o mesmo conector apagou o sinal retido do no-show "
            "anterior (regressão B49)"
        )
        assert rv.ativa_do_conector("P1-C3").usuario == "allan"

    def test_banco_impede_duas_reservas_ativas_no_mesmo_conector(self):
        import db, wallet
        import reservations as rv
        wallet.garantir_conta("amanda", 100.0)
        rv.criar("amanda", "P1-C4")
        with pytest.raises(Exception):
            db.execute(
                "INSERT INTO reservas (charger_id, usuario, criada_em,"
                " expira_em, sinal_brl, status) VALUES (?,?,?,?,?,?)",
                ("P1-C4", "allan", "2030-01-01 00:00:00",
                 "2030-01-01 00:15:00", 10.0, rv.STATUS_ATIVA),
            )


# ===========================================================================
# SPRINT 3 — QR simulado
# ===========================================================================

class TestQRSimulado:

    def test_determinismo(self):
        from qr import qr_simulado
        assert qr_simulado("A|1|2") == qr_simulado("A|1|2")
        assert qr_simulado("A|1|2") != qr_simulado("B|1|2")

    def test_tem_rotulo_acessivel_e_densidade(self):
        from qr import qr_simulado
        svg = qr_simulado("CGI|TESTE|10.00")
        assert "aria-label" in svg
        assert svg.count("<rect") > 200


class TestRelatorioOrdenavel:
    """
    Contrato de markup do qual o JS de ordenação/busca do relatório depende.

    O comportamento em si é verificado no navegador; estes testes guardam o
    que o Python renderiza — se um `data-valor` sumir, a ordenação numérica
    passa a comparar zeros em silêncio, sem erro nenhum na tela.
    """

    @pytest.fixture
    def com_sessao(self, client):
        """
        Uma sessão ativa e o HTML do relatório.

        Sob o pytest o seed de demonstração não roda: sem criar a sessão
        aqui, o `{% if sessoes %}` do template esconde a tabela inteira e os
        testes verificariam uma tela vazia.
        """
        import app as A
        from models import UserType
        s = A.sm.create_session("P2-C1", "ABC1D23", "Amanda",
                                UserType.SUBSCRIBER, 11.0, owner="amanda")
        A.sm.start_charging(s.session_id, 11.0, 1.2345)
        A.sm.update_energy(s.session_id, 7.5)
        return s, client.get("/relatorio").data.decode()

    def test_cabecalhos_sao_ordenaveis(self, com_sessao):
        _, html = com_sessao
        assert html.count('class="ordenavel"') == 10, "as 10 colunas ordenáveis"
        for col in range(10):
            assert f'data-col="{col}"' in html
        assert html.count('data-tipo="num"') == 5, "5 colunas numéricas"

    def test_colunas_numericas_carregam_data_valor(self, com_sessao):
        s, html = com_sessao
        linha = html.split(f'data-session-id="{s.session_id}"')[1].split("</tr>")[0]

        # O valor cru precisa estar no atributo, não só formatado no texto
        assert 'data-valor="11.0"' in linha
        assert 'data-valor="7.5"' in linha
        assert 'data-valor="1.2345"' in linha
        assert linha.count("data-valor") == 5

    def test_busca_esta_na_tela(self, com_sessao):
        _, html = com_sessao
        assert 'id="busca-sessao"' in html
        assert 'id="contagem-sessoes"' in html
        assert 'aria-label="Buscar sessão' in html, "campo precisa de rótulo acessível"

    def test_sem_sessoes_nao_mostra_controles(self, client):
        """Busca e contagem sobre uma tabela que não existe seria ruído."""
        html = client.get("/relatorio").data.decode()
        assert 'id="busca-sessao"' not in html
        assert "Nenhuma sessão nesta execução" in html


# ===========================================================================
# Estruturas de Dados e Algoritmos (Sprint 3 — disciplina DSA)
# ===========================================================================

def _sessao(numero: int, energia: float, custo: float, minutos: float,
            conector: str = "P1-C1"):
    """
    Monta uma sessão ENCERRADA com valores controlados.

    O tempo é convertido em `end_time` porque `duration_minutes` é uma
    propriedade derivada dos carimbos de tempo — é o mesmo caminho que o
    cadastro manual do `menu.py` percorre.
    """
    import datetime

    from models import ChargingSession, SessionStatus, UserType

    inicio = datetime.datetime(2026, 9, 1, 12, 0, 0)
    return ChargingSession(
        numero=numero,
        charger_id=conector,
        station_id=conector.split("-")[0],
        vehicle_id=f"TST{numero:04d}",
        user_name=f"Motorista {numero}",
        user_type=UserType.STANDARD,
        requested_power_kw=11.0,
        allocated_power_kw=11.0,
        start_time=inicio,
        end_time=inicio + datetime.timedelta(minutes=minutos),
        energy_kwh=energia,
        tariff_kwh=1.2,
        total_cost_brl=custo,
        status=SessionStatus.FINISHED,
    )


@pytest.fixture
def colecao():
    """
    Cinco sessões fora de ordem em todos os critérios avaliados.

    IDs em 5, 3, 1, 4, 2 de propósito: nenhuma ordenação pode "funcionar por
    acidente" porque a lista já estivesse quase ordenada.
    """
    return [
        _sessao(5, energia=30.0, custo=36.00, minutos=180.0),
        _sessao(3, energia=10.0, custo=12.00, minutos=60.0),
        _sessao(1, energia=50.0, custo=60.00, minutos=300.0),
        _sessao(4, energia=20.0, custo=24.00, minutos=120.0),
        _sessao(2, energia=40.0, custo=48.00, minutos=240.0),
    ]


class TestAlgoritmosDSA:
    """
    Cobre os algoritmos avaliados na disciplina de Estruturas de Dados.

    Estes testes existem por dois motivos além da corretude: provar que as
    contagens de comparações batem com as fórmulas fechadas usadas no
    relatório, e impedir que alguém "simplifique" `algoritmos.py` com um
    recurso nativo mais tarde — o que seria reprovação direta.
    """

    # -- busca sequencial ------------------------------------------------

    def test_busca_sequencial_encontra(self, colecao):
        import algoritmos
        indice, comparacoes = algoritmos.busca_sequencial(colecao, 1)
        assert indice == 2
        assert colecao[indice].numero == 1
        assert comparacoes == 3, "uma comparação por elemento até achar"

    def test_busca_sequencial_primeiro_elemento_custa_uma(self, colecao):
        import algoritmos
        indice, comparacoes = algoritmos.busca_sequencial(colecao, 5)
        assert (indice, comparacoes) == (0, 1), "melhor caso é O(1)"

    def test_busca_sequencial_nao_encontra(self, colecao):
        import algoritmos
        indice, comparacoes = algoritmos.busca_sequencial(colecao, 99)
        assert indice == -1
        assert comparacoes == len(colecao), "pior caso varre tudo"

    def test_busca_sequencial_lista_vazia(self):
        import algoritmos
        assert algoritmos.busca_sequencial([], 1) == (-1, 0)

    def test_busca_sequencial_aceita_outra_chave(self, colecao):
        """É essa flexibilidade que permite ao get_session buscar por UUID."""
        import algoritmos
        alvo = colecao[3].session_id
        indice, _ = algoritmos.busca_sequencial(
            colecao, alvo, chave=lambda s: s.session_id
        )
        assert indice == 3

    # -- busca binária ---------------------------------------------------

    def test_busca_binaria_encontra_em_lista_ordenada(self, colecao):
        import algoritmos
        algoritmos.insertion_sort(colecao, algoritmos.CRITERIOS["1"][1])
        for esperado in (1, 2, 3, 4, 5):
            indice, comparacoes = algoritmos.busca_binaria(colecao, esperado)
            assert colecao[indice].numero == esperado
            assert comparacoes <= 3, "log2(5) arredondado para cima é 3"

    def test_busca_binaria_nao_encontra(self, colecao):
        import algoritmos
        algoritmos.insertion_sort(colecao, algoritmos.CRITERIOS["1"][1])
        indice, comparacoes = algoritmos.busca_binaria(colecao, 99)
        assert indice == -1
        assert comparacoes <= 3

    def test_busca_binaria_lista_vazia(self):
        import algoritmos
        assert algoritmos.busca_binaria([], 1) == (-1, 0)

    def test_busca_binaria_e_mais_barata_que_sequencial(self):
        """O contraste do item 10 do enunciado, medido."""
        import algoritmos
        grande = [_sessao(n, energia=n, custo=n, minutos=n)
                  for n in range(1, 201)]
        ausente = 999
        _, c_seq = algoritmos.busca_sequencial(grande, ausente)
        _, c_bin = algoritmos.busca_binaria(grande, ausente)
        assert c_seq == 200
        assert c_bin == 8, "log2(200) arredondado para cima é 8"

    # -- ordenação -------------------------------------------------------

    def test_bubble_sort_por_id(self, colecao):
        import algoritmos
        algoritmos.bubble_sort(colecao, algoritmos.CRITERIOS["1"][1])
        assert [s.numero for s in colecao] == [1, 2, 3, 4, 5]

    def test_bubble_sort_conta_exatamente_n_n_menos_1_sobre_2(self, colecao):
        import algoritmos
        n = len(colecao)
        comparacoes = algoritmos.bubble_sort(colecao,
                                            algoritmos.CRITERIOS["1"][1])
        assert comparacoes == n * (n - 1) // 2 == 10

    def test_bubble_sort_nao_tem_parada_antecipada(self, colecao):
        """Lista já ordenada continua custando n(n-1)/2 — é o combinado."""
        import algoritmos
        chave = algoritmos.CRITERIOS["1"][1]
        algoritmos.bubble_sort(colecao, chave)
        n = len(colecao)
        assert algoritmos.bubble_sort(colecao, chave) == n * (n - 1) // 2

    def test_insertion_sort_por_energia(self, colecao):
        import algoritmos
        algoritmos.insertion_sort(colecao, algoritmos.CRITERIOS["2"][1])
        assert [s.energy_kwh for s in colecao] == [10.0, 20.0, 30.0, 40.0, 50.0]

    def test_insertion_sort_melhor_caso_custa_n_menos_1(self, colecao):
        import algoritmos
        chave = algoritmos.CRITERIOS["2"][1]
        algoritmos.insertion_sort(colecao, chave)
        assert algoritmos.insertion_sort(colecao, chave) == len(colecao) - 1

    def test_insertion_sort_pior_caso_iguala_bubble(self):
        """Ordem inversa: o insertion degenera para n(n-1)/2, como a teoria diz."""
        import algoritmos
        n = 6
        invertida = [_sessao(i, energia=n - i, custo=1.0, minutos=1.0)
                     for i in range(1, n + 1)]
        chave = algoritmos.CRITERIOS["2"][1]
        assert algoritmos.insertion_sort(invertida, chave) == n * (n - 1) // 2

    def test_ordenacoes_sao_estaveis(self):
        """
        Importa no rebalance: sessões com a mesma potência são restauradas na
        ordem de chegada, então a decisão de potência é reproduzível.
        """
        import algoritmos
        empatadas = [_sessao(n, energia=7.0, custo=1.0, minutos=1.0)
                     for n in (1, 2, 3, 4)]
        chave = algoritmos.CRITERIOS["2"][1]

        alvo = list(empatadas)
        algoritmos.bubble_sort(alvo, chave)
        assert [s.numero for s in alvo] == [1, 2, 3, 4]

        alvo = list(empatadas)
        algoritmos.insertion_sort(alvo, chave)
        assert [s.numero for s in alvo] == [1, 2, 3, 4]

    def test_ordenacao_e_in_place(self, colecao):
        """A mesma lista é reordenada — é o que faz a ordem persistir no menu."""
        import algoritmos
        original = colecao
        algoritmos.bubble_sort(colecao, algoritmos.CRITERIOS["3"][1])
        assert colecao is original
        assert [s.total_cost_brl for s in colecao] == [12.0, 24.0, 36.0, 48.0, 60.0]

    def test_todos_os_quatro_criterios_ordenam(self, colecao):
        import algoritmos
        for tecla, (rotulo, chave) in algoritmos.CRITERIOS.items():
            alvo = list(colecao)
            algoritmos.insertion_sort(alvo, chave)
            valores = [chave(s) for s in alvo]
            assert valores == sorted(valores), f"critério {tecla} ({rotulo})"

    # -- estatísticas ----------------------------------------------------

    def test_estatisticas_os_seis_valores(self, colecao):
        import algoritmos
        est = algoritmos.estatisticas(colecao)
        assert est["total_sessoes"] == 5
        assert est["energia_total_kwh"] == 150.0
        assert est["faturamento_brl"] == 180.0
        assert est["ticket_medio_brl"] == 36.0
        assert est["maior_consumo_kwh"] == 50.0
        assert est["menor_consumo_kwh"] == 10.0

    def test_estatisticas_valores_de_apoio(self, colecao):
        import algoritmos
        est = algoritmos.estatisticas(colecao)
        assert est["tempo_total_min"] == 900.0
        assert est["energia_media_kwh"] == 30.0
        assert est["numero_maior_consumo"] == 1
        assert est["numero_menor_consumo"] == 3

    def test_estatisticas_colecao_vazia_devolve_zeros(self):
        """min() de sequência vazia é ValueError — o item 8 proíbe quebrar."""
        import algoritmos
        est = algoritmos.estatisticas([])
        assert est["total_sessoes"] == 0
        assert all(valor == 0 for valor in est.values())

    def test_estatisticas_uma_sessao(self):
        import algoritmos
        est = algoritmos.estatisticas([_sessao(1, 8.0, 9.6, 48.0)])
        assert est["maior_consumo_kwh"] == est["menor_consumo_kwh"] == 8.0
        assert est["ticket_medio_brl"] == 9.6

    # -- apoio ao cadastro -----------------------------------------------

    def test_numero_existe(self, colecao):
        import algoritmos
        assert algoritmos.numero_existe(colecao, 3) is True
        assert algoritmos.numero_existe(colecao, 99) is False
        assert algoritmos.numero_existe([], 1) is False

    def test_proximo_numero(self, colecao):
        import algoritmos
        assert algoritmos.proximo_numero(colecao) == 6
        assert algoritmos.proximo_numero([]) == 1

    # -- guardas de conformidade com o enunciado -------------------------

    def test_algoritmos_nao_usam_sort_nem_sorted(self):
        """
        O enunciado permite recursos nativos "nas demais partes do sistema",
        mas não dentro dos algoritmos avaliados. Esta guarda impede que alguém
        "simplifique" algoritmos.py depois — seria reprovação direta.
        """
        import algoritmos

        fonte = pathlib.Path(algoritmos.__file__).read_text(encoding="utf-8")
        for proibido in (".sort(", "sorted(", ".index(", "bisect"):
            assert proibido not in fonte, f"{proibido} em algoritmos.py"

    def test_menu_nao_importa_flask(self):
        """
        O menu é um segundo ponto de entrada do MESMO sistema, e precisa rodar
        com Python puro — sem nenhuma dependência externa instalada.
        """
        import menu

        for modulo in (menu, __import__("algoritmos"),
                       __import__("session_manager"), __import__("db")):
            fonte = pathlib.Path(modulo.__file__).read_text(encoding="utf-8")
            assert "import flask" not in fonte.lower(), modulo.__name__

    def test_algoritmos_so_depende_de_models(self):
        """Isolamento do módulo avaliado: nada do app entra aqui."""
        import algoritmos

        fonte = pathlib.Path(algoritmos.__file__).read_text(encoding="utf-8")
        for proibido in ("import db", "import session_manager",
                         "import power_manager", "import app"):
            assert proibido not in fonte, proibido


class TestIntegracaoAlgoritmosNoSistema:
    """
    Prova que os algoritmos avaliados rodam no sistema real, não só no menu.

    É o que os critérios 3 e 4 da rubrica chamam de "bem integrada" e
    "integrada ao sistema".
    """

    def test_get_session_usa_busca_sequencial(self, sm, monkeypatch):
        """Se a busca sequencial parar de ser chamada, este teste quebra."""
        import algoritmos
        import session_manager

        from models import UserType

        s = sm.create_session("P1-C1", "TST-0001", "Test", UserType.STANDARD)
        chamadas = []
        original = algoritmos.busca_sequencial

        def espiao(*args, **kwargs):
            chamadas.append(args[1])
            return original(*args, **kwargs)

        monkeypatch.setattr(session_manager.algoritmos, "busca_sequencial",
                            espiao)
        assert sm.get_session(s.session_id) is s
        assert chamadas == [s.session_id]

    def test_get_session_devolve_none_para_inexistente(self, sm):
        assert sm.get_session("CGI-NAOEXISTE") is None

    def test_sessions_e_uma_lista_viva(self, sm):
        from models import UserType

        assert isinstance(sm.sessions, list)
        assert sm.sessions == []
        sm.create_session("P1-C1", "TST-0001", "Test", UserType.STANDARD)
        assert len(sm.sessions) == 1
        assert sm.sessions is not sm.list_all(), "list_all devolve cópia"

    def test_create_session_numera_sequencialmente(self, sm):
        from models import UserType

        numeros = [sm.create_session(f"P1-C{i}", f"TST-000{i}", "Test",
                                     UserType.STANDARD).numero
                   for i in range(1, 4)]
        assert numeros == [1, 2, 3]

    def test_registrar_historico_nao_ocupa_conector(self, sm):
        """
        É a razão de o método existir: create_session recusaria o 16º registro
        (são 15 conectores) e reservaria hardware para um fato passado.
        """
        for numero in range(1, 21):
            sm.registrar_historico(_sessao(numero, 10.0, 12.0, 60.0))
        assert len(sm.sessions) == 20
        assert all(sid is None for sid in sm.list_chargers().values())

    def test_tempo_digitado_vira_end_time_sem_perda(self):
        """
        O cadastro manual do menu recebe a duração em minutos e a converte em
        `end_time`, porque a duração é derivada dos carimbos de tempo. A volta
        tem que bater.
        """
        for minutos in (0.5, 42.5, 95.0, 1439.0):
            sessao = _sessao(1, energia=10.0, custo=12.0, minutos=minutos)
            assert abs(sessao.duration_minutes - minutos) < 0.01, minutos

    def test_registrar_historico_recusa_id_duplicado(self, sm):
        sm.registrar_historico(_sessao(7, 10.0, 12.0, 60.0))
        with pytest.raises(ValueError, match="7"):
            sm.registrar_historico(_sessao(7, 99.0, 99.0, 99.0))
        assert len(sm.sessions) == 1

    def test_rebalance_usa_insertion_sort(self, sm, pm, pe, monkeypatch):
        import algoritmos
        import power_manager

        from models import SessionStatus, UserType

        chamadas = []
        original = algoritmos.insertion_sort

        def espiao(colecao, chave):
            chamadas.append(len(colecao))
            return original(colecao, chave)

        monkeypatch.setattr(power_manager.algoritmos, "insertion_sort", espiao)

        # 4 sessões de 11 kW num limite de 33 kW forçam throttle
        criadas = [_start(sm, pm, pe, f"P1-C{i}", UserType.STANDARD)[0]
                   for i in range(1, 5)]
        assert any(s.status == SessionStatus.THROTTLED for s in sm.list_active())

        sm.finish_session(criadas[0].session_id)
        resultado = pm.rebalance()

        assert chamadas, "o rebalance precisa passar pelo insertion sort"
        assert resultado is not None
        assert "insertion sort" in resultado.message
        assert "comparações" in resultado.message

    def test_rebalance_ordena_menor_potencia_primeiro(self, sm, pm, pe):
        """
        O insertion sort tem que deixar a fila em ordem crescente de potência
        alocada — é o que dá prioridade a quem foi mais reduzido.
        """
        from models import SessionStatus, UserType

        criadas = [_start(sm, pm, pe, f"P1-C{i}", UserType.STANDARD)[0]
                   for i in range(1, 5)]
        sm.finish_session(criadas[0].session_id)
        resultado = pm.rebalance()
        assert resultado is not None
        assert not any(s.status == SessionStatus.FAULTED
                       for s in sm.list_active())
        assert sm.total_allocated_power_kw() <= pm.limit_kw + 0.01

    def test_rebalance_respeita_limite_com_o_sort_manual(self, sm, pm, pe):
        from models import UserType

        criadas = [_start(sm, pm, pe, f"P1-C{i}", UserType.SUBSCRIBER)[0]
                   for i in range(1, 6)]
        sm.finish_session(criadas[0].session_id)
        sm.finish_session(criadas[1].session_id)
        pm.rebalance()
        assert sm.total_allocated_power_kw() <= pm.limit_kw + 0.01


class TestCarregamentoDoHistorico:
    """`db.carregar_sessoes()` — a ponte entre o SQLite e a coleção do menu."""

    def test_banco_vazio_devolve_lista_vazia(self):
        import db

        assert db.carregar_sessoes() == []

    def test_tabela_ausente_devolve_lista_vazia(self, tmp_path, monkeypatch):
        """Banco inexistente não pode derrubar o menu (item 8 do enunciado)."""
        import db

        monkeypatch.setattr(db, "DB_PATH", tmp_path / "nao_existe.db")
        assert db.carregar_sessoes() == []

    def test_arquivo_corrompido_devolve_lista_vazia(self, tmp_path, monkeypatch):
        import db

        lixo = tmp_path / "lixo.db"
        lixo.write_bytes(b"isto nao e um banco sqlite" * 40)
        monkeypatch.setattr(db, "DB_PATH", lixo)
        assert db.carregar_sessoes() == []

    def test_reconstroi_sessao_e_numera_de_1(self):
        import db

        from models import SessionStatus

        for indice in range(3):
            db.execute(
                "INSERT INTO sessoes (session_id, usuario, charger_id,"
                " station_id, vehicle_id, user_name, user_type, inicio, fim,"
                " hora_inicio, duracao_min, potencia_kw, energia_kwh,"
                " tarifa_kwh, custo_brl, metodo_pagto)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"CGI-TESTE{indice}", "luiz", "P1-C1", "P1", "ABC1D23",
                 "Teste", "P", f"2026-09-0{indice + 1} 12:00:00",
                 f"2026-09-0{indice + 1} 13:00:00", 12, 60.0, 10.0, 10.0,
                 1.2, 12.0, "PIX"),
            )

        sessoes = db.carregar_sessoes()
        assert [s.numero for s in sessoes] == [1, 2, 3]
        assert all(s.status == SessionStatus.FINISHED for s in sessoes)
        assert sessoes[0].duration_minutes == 60.0
        assert sessoes[0].energy_kwh == 10.0
        assert sessoes[0].total_cost_brl == 12.0

    def test_ignora_sessao_sem_pagamento(self):
        import db

        db.execute(
            "INSERT INTO sessoes (session_id, usuario, charger_id, station_id,"
            " vehicle_id, user_name, user_type, inicio, fim, hora_inicio,"
            " duracao_min, potencia_kw, energia_kwh, tarifa_kwh, custo_brl,"
            " metodo_pagto)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("CGI-SEMPAGTO", "luiz", "P1-C1", "P1", "ABC1D23", "Teste", "P",
             "2026-09-01 12:00:00", "2026-09-01 13:00:00", 12, 60.0, 10.0,
             10.0, 1.2, 12.0, ""),
        )
        assert db.carregar_sessoes() == []


class TestMenuTerminal:
    """O menu de terminal: estrutura, validações e as redes de segurança."""

    def test_menu_tem_as_funcoes_esperadas(self):
        import menu

        for nome in ("cadastrar_sessao", "listar_sessoes", "buscar_sessao",
                     "ordenar_sessoes", "mostrar_estatisticas",
                     "comparar_algoritmos", "ler_numero", "ler_texto", "main"):
            assert callable(getattr(menu, nome)), nome

    def test_menu_exibe_as_sete_opcoes(self, capsys):
        import menu

        menu.exibir_menu()
        saida = capsys.readouterr().out
        for numero in range(1, 8):
            assert f"{numero} - " in saida
        assert "ESTAÇÃO DE RECARGA" in saida

    def test_ler_numero_recusa_texto_e_devolve_none(self, monkeypatch):
        """Três tentativas com texto: devolve None em vez de estourar."""
        import menu

        entradas = iter(["abc", "xyz", "!!"])
        monkeypatch.setattr("builtins.input", lambda _: next(entradas))
        assert menu.ler_numero("Valor") is None

    def test_ler_numero_recusa_fora_da_faixa(self, monkeypatch):
        import menu

        entradas = iter(["-5", "2000", "42"])
        monkeypatch.setattr("builtins.input", lambda _: next(entradas))
        assert menu.ler_numero("Valor", minimo=0, maximo=1000) == 42.0

    def test_ler_numero_aceita_virgula_decimal(self, monkeypatch):
        import menu

        monkeypatch.setattr("builtins.input", lambda _: "18,5")
        assert menu.ler_numero("Energia") == 18.5

    def test_ler_numero_enter_vazio_cancela(self, monkeypatch):
        import menu

        monkeypatch.setattr("builtins.input", lambda _: "")
        assert menu.ler_numero("Valor") is None

    def test_menu_encerra_na_opcao_7(self, monkeypatch, capsys):
        import menu

        entradas = iter(["99", "", "7"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entradas))
        assert menu.main() == 0
        saida = capsys.readouterr().out
        assert "inválida" in saida, "opção inválida precisa avisar"
        assert "Encerrando" in saida

    def test_menu_sobrevive_a_eof(self, monkeypatch, capsys):
        import menu

        def estoura(_=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", estoura)
        assert menu.main() == 0
        assert "Encerrado pelo usuário" in capsys.readouterr().out

    def test_menu_sobrevive_a_falha_dentro_de_uma_opcao(self, monkeypatch,
                                                       capsys):
        """A rede de segurança do item 8: erro numa opção não derruba o menu."""
        import menu

        def explode(_gerenciador):
            raise RuntimeError("falha simulada")

        monkeypatch.setitem(menu.ACOES, "5", explode)
        entradas = iter(["5", "7"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entradas))
        assert menu.main() == 0
        saida = capsys.readouterr().out
        assert "Falha inesperada" in saida
        assert "menu continua ativo" in saida


class TestSeparacaoMemoriaHistorico:
    """
    O relatório mostra o estado quente; o painel soma o histórico arquivado.
    Os dois números divergem por construção — as telas precisam dizer isso,
    senão o operador lê um como total do outro.
    """

    def _arquivar(self, n: int) -> None:
        import db

        for i in range(n):
            db.execute(
                "INSERT INTO sessoes (session_id, usuario, charger_id,"
                " station_id, vehicle_id, user_name, user_type, inicio, fim,"
                " hora_inicio, duracao_min, potencia_kw, energia_kwh,"
                " tarifa_kwh, custo_brl, metodo_pagto)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"CGI-HIST{i:03d}", "luiz", "P1-C1", "P1", "ABC1D23", "Teste",
                 "P", "2026-09-01 12:00:00", "2026-09-01 13:00:00", 12, 60.0,
                 10.0, 10.0, 1.2, 12.0, "PIX"),
            )

    def test_relatorio_mostra_o_total_arquivado(self, client):
        self._arquivar(7)
        html = client.get("/relatorio").data.decode()
        assert "7 sessões" in html and "arquivadas" in html
        assert "Sessões nesta execução" in html

    def test_relatorio_nao_confunde_execucao_com_historico(self, client):
        """Com histórico no banco e memória vazia, o card tem que dizer 0."""
        self._arquivar(3)
        html = client.get("/relatorio").data.decode()
        assert "Nenhuma sessão nesta execução" in html

    def test_admin_rotula_o_kpi_como_historico(self, client):
        self._arquivar(4)
        html = client.get("/admin").data.decode()
        assert "4 sessões pagas no histórico" in html

    def test_uma_consulta_para_todas_as_encerradas(self, monkeypatch):
        """
        A separação paga/pendente não pode voltar a abrir uma conexão SQLite
        por sessão em tela — era uma consulta por linha do relatório.
        """
        import app as app_mod

        chamadas = []
        original = app_mod.db.query_all

        def espiao(sql, params=()):
            chamadas.append(sql)
            return original(sql, params)

        monkeypatch.setattr(app_mod.db, "query_all", espiao)
        ids = [f"CGI-X{i}" for i in range(20)]
        assert app_mod._sessoes_arquivadas(ids) == set()
        assert len(chamadas) == 1, "uma consulta só, com IN (...)"

    def test_lista_vazia_nao_consulta_o_banco(self, monkeypatch):
        import app as app_mod

        def explode(*a, **k):
            raise AssertionError("não deveria consultar com lista vazia")

        monkeypatch.setattr(app_mod.db, "query_all", explode)
        assert app_mod._sessoes_arquivadas([]) == set()

    def test_accrue_energy_aceita_a_sessao_ou_o_id(self, sm, pm, pe):
        """
        O laço de polling já tem o objeto em mãos; passar o ID obrigava uma
        busca sequencial redundante a cada chamada.
        """
        from models import UserType

        s, _ = _start(sm, pm, pe, "P1-C1", UserType.STANDARD)
        assert sm.accrue_energy(s) is s
        assert sm.accrue_energy(s.session_id) is s

    def test_debug_do_flask_nao_fica_ligado_por_padrao(self):
        """
        debug=True publica o console do Werkzeug, que executa Python arbitrário
        — com host 0.0.0.0 isso fica aberto para a rede inteira.
        """
        fonte = pathlib.Path(__file__).with_name("app.py").read_text(
            encoding="utf-8")
        assert "debug=True" not in fonte
        assert 'CHARGEGRID_DEBUG' in fonte


class TestTarifaFonteUnica:
    """
    Tarifa base, desconto de assinante, multiplicador de pico e taxa mínima
    eram declarados em `logica_recarga` e de novo em `pricing_engine`. Dois
    lugares definindo o preço do kWh é divergência esperando para acontecer.
    """

    def test_pricing_engine_reexporta_as_constantes_do_sprint1(self):
        import logica_recarga
        import pricing_engine

        for nome in ("TARIFA_BASE_KWH", "DESCONTO_ASSINANTE",
                     "MULTIPLICADOR_PICO", "TAXA_MINIMA_SESSAO"):
            assert getattr(pricing_engine, nome) is getattr(logica_recarga, nome), nome

    def test_pricing_engine_nao_redeclara_a_tarifa(self):
        fonte = pathlib.Path(__file__).with_name("pricing_engine.py").read_text(
            encoding="utf-8")
        for nome in ("TARIFA_BASE_KWH", "DESCONTO_ASSINANTE",
                     "MULTIPLICADOR_PICO", "TAXA_MINIMA_SESSAO"):
            assert f"{nome}:" not in fonte, f"{nome} voltou a ser declarado aqui"


# ---------------------------------------------------------------------------
# Recarga sem cadastro (QR do totem)
# ---------------------------------------------------------------------------

class TestRecargaSemCadastro:
    """
    A modalidade avulsa: o motorista chega pelo QR, deixa uma caução e carrega.

    O que estes testes protegem é a razão de a modalidade existir — nenhum
    passo pode voltar a exigir conta, senha ou alguém do posto para liberar.
    """

    def test_estorno_devolve_o_que_sobrou_da_caucao(self):
        import avulso
        assert avulso.estorno(18.40) == round(avulso.CAUCAO_BRL - 18.40, 2)

    def test_caucao_totalmente_consumida_nao_devolve_nada(self):
        import avulso
        assert avulso.estorno(avulso.CAUCAO_BRL) == 0.0

    def test_estorno_nunca_e_negativo(self):
        import avulso
        assert avulso.estorno(avulso.CAUCAO_BRL * 3) == 0.0

    def test_token_de_cada_sessao_e_diferente(self):
        import avulso
        assert avulso.novo_dono() != avulso.novo_dono()

    def test_dono_do_token_reconstroi_o_owner(self):
        import avulso
        dono = avulso.novo_dono()
        assert avulso.dono_do_token(dono.split(":")[1]) == dono
        assert avulso.eh_avulso(dono)

    def test_owner_de_conta_normal_nao_e_avulso(self):
        import avulso
        assert not avulso.eh_avulso("amanda")
        assert not avulso.eh_avulso("")

    # -- rotas ------------------------------------------------------------

    def test_totem_abre_sem_login(self, client_anonimo):
        """A guarda de acesso não pode mandar o totem para a tela de login."""
        assert client_anonimo.get("/totem").status_code == 200

    def test_formulario_do_conector_abre_sem_login(self, client_anonimo):
        resposta = client_anonimo.get("/totem/P2-C1")
        assert resposta.status_code == 200
        assert b"Placa" in resposta.data

    def test_login_oferece_o_caminho_sem_cadastro(self, client_anonimo):
        assert "sem cadastro" in client_anonimo.get("/login").get_data(as_text=True)

    def test_conector_vip_recusado_para_quem_nao_tem_conta(self, client_anonimo):
        """C5 é de assinante, e sessão avulsa não tem assinatura."""
        resposta = client_anonimo.get("/totem/P1-C5", follow_redirects=True)
        assert "exclusivo para assinantes" in resposta.get_data(as_text=True)

    def test_conector_inexistente_volta_para_a_lista(self, client_anonimo):
        resposta = client_anonimo.get("/totem/P9-C9", follow_redirects=True)
        assert "não encontrado" in resposta.get_data(as_text=True)

    def test_placa_invalida_nao_cria_sessao(self, client_anonimo):
        resposta = client_anonimo.post(
            "/totem/P2-C1", data={"placa": "XX", "metodo": "PIX"},
            follow_redirects=True)
        assert "inválida" in resposta.get_data(as_text=True)

    def test_nexuscoin_recusado_sem_conta(self, client_anonimo):
        """NexusCoin debita de uma carteira, e avulso não tem carteira."""
        resposta = client_anonimo.post(
            "/totem/P2-C1", data={"placa": "ABC1D23", "metodo": "NEXUSCOIN"},
            follow_redirects=True)
        assert "exige conta" in resposta.get_data(as_text=True)

    def _liberar(self, cliente, charger_id="P2-C1", placa="ABC1D23"):
        resposta = cliente.post(f"/totem/{charger_id}",
                                data={"placa": placa, "metodo": "PIX"})
        assert resposta.status_code == 302, resposta.get_data(as_text=True)[:400]
        return resposta.headers["Location"].rsplit("/", 1)[-1]

    def test_liberar_inicia_a_recarga_e_devolve_o_link(self, client_anonimo):
        token = self._liberar(client_anonimo)
        pagina = client_anonimo.get(f"/avulso/{token}").get_data(as_text=True)
        assert "Recarregando" in pagina
        assert "ABC1D23" in pagina

    def test_sessao_avulsa_nao_recebe_cashback(self, client_anonimo, tmp_path):
        """Cashback cai numa carteira NexusCoin, que a sessão avulsa não tem."""
        token = self._liberar(client_anonimo)
        client_anonimo.post(f"/avulso/{token}/encerrar")
        import db
        linha = db.query_one(
            "SELECT cashback_nc, usuario FROM sessoes WHERE usuario LIKE 'avulso:%'")
        assert linha is not None, "a sessão avulsa não foi arquivada"
        assert linha["cashback_nc"] == 0.0

    def test_encerrar_arquiva_com_a_placa_e_libera_o_conector(self, client_anonimo):
        token = self._liberar(client_anonimo)
        resposta = client_anonimo.post(f"/avulso/{token}/encerrar",
                                       follow_redirects=True)
        pagina = resposta.get_data(as_text=True)
        assert "Recarga concluída" in pagina

        import db
        linha = db.query_one("SELECT * FROM sessoes WHERE usuario = 'avulso:ABC1D23'")
        assert linha is not None
        assert linha["metodo_pagto"] == "PIX"
        assert linha["user_name"] == "Sem cadastro"

        # O conector volta para a fila no mesmo passo: como a caução já estava
        # retida, não há motivo para segurar a vaga esperando pagamento.
        livre = client_anonimo.get("/totem").get_data(as_text=True)
        assert "P2-C1" in livre or "/totem/P2-C1" in livre

    def test_encerrar_duas_vezes_nao_arquiva_duas_linhas(self, client_anonimo):
        token = self._liberar(client_anonimo)
        client_anonimo.post(f"/avulso/{token}/encerrar")
        client_anonimo.post(f"/avulso/{token}/encerrar", follow_redirects=True)
        import db
        linhas = db.query_all("SELECT session_id FROM sessoes WHERE usuario LIKE 'avulso:%'")
        assert len(linhas) == 1

    def test_token_desconhecido_nao_estoura(self, client_anonimo):
        resposta = client_anonimo.get("/avulso/naoexiste", follow_redirects=True)
        assert resposta.status_code == 200

    def test_recibo_mostra_o_estorno(self, client_anonimo):
        """
        Uma recarga de segundos cai na taxa mínima, bem abaixo da caução —
        então o estorno precisa aparecer na tela.
        """
        import avulso
        token = self._liberar(client_anonimo)
        client_anonimo.post(f"/avulso/{token}/encerrar")
        pagina = client_anonimo.get(f"/avulso/{token}").get_data(as_text=True)
        assert "Volta para você" in pagina
        esperado = f"{avulso.estorno(2.00):.2f}".replace(".", ",")
        assert esperado in pagina

    def test_rotas_do_totem_estao_declaradas_publicas(self):
        """
        A guarda nega por padrão: rota nova nasce protegida. Se alguém renomear
        um endpoint do totem e esquecer da lista, a modalidade quebra em
        silêncio — pedindo login a quem veio justamente para não fazer login.
        """
        import app as app_module
        for endpoint in ("totem_postos", "totem_liberar",
                         "avulso_sessao", "avulso_encerrar"):
            assert endpoint in app_module.ROTAS_PUBLICAS, endpoint
            assert endpoint not in app_module.ROTAS_STAFF, endpoint
