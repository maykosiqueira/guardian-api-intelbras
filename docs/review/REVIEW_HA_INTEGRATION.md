# Revisão da Documentação × Código — Integração Home Assistant (Intelbras Guardian)

**Agente:** rev-ha · **Escopo:** documentação da integração HA (`custom_components/intelbras_guardian/`) versus o código real.
**Código de referência (somente leitura):** `custom_components/intelbras_guardian/*.py`, `manifest.json`, `strings.json`; `hacs.json`.
**Docs auditados/editáveis:** `README.md`, `INSTALACAO.md`, `IMPLEMENTATION_STATUS.md`, `FASE1_VALIDATION.md`, `RESUMO_PROJETO.md`, `CHANGELOG.md`, `hacs.json`, `manifest.json`, `strings.json`.

---

## 0. Mapa do código real (fonte da verdade)

| Item | Realidade no código | Evidência |
|------|---------------------|-----------|
| Plataformas registradas | `alarm_control_panel`, `binary_sensor`, `button`, `event`, `sensor`, `switch` (6) | `const.py:73` |
| Painel de partição individual | Feature **apenas `ARM_AWAY`** (+ disarm); código sem código de usuário | `alarm_control_panel.py:158`, `154-155` |
| Painel unificado | Feature `ARM_HOME \| ARM_AWAY`; sem código | `alarm_control_panel.py:641-644`, `639-640` |
| Arme "Ausente" atômico | Se houver zona aberta, **não arma nada**, fica em `ARMING` e envia notificação acionável | `alarm_control_panel.py:1245-1269` |
| Bypass + re-arme | Notificação "Ignorar Zonas e Armar"; contexto expira em 300s (5 min) | `__init__.py:67`, `__init__.py:18` |
| Sensor `Last Event` | `{mac}_last_event`, nome "Last Event" | `sensor.py:97`, `112` |
| Sensor `Último Disparo` (novo) | `{mac}_last_trigger`, nome "Último Disparo"; zona do disparo real | `sensor.py:192`, `205` |
| Sensor sinal wireless | `{mac}_zone_{i}_signal`, unidade `/10` | `sensor.py:265`, `281` |
| Binary sensor zona / bateria | `{mac}_zone_{i}` / `{mac}_zone_{i}_battery` (device_class battery) | `binary_sensor.py:107`, `197/212` |
| Entidade `event` por zona | `{mac}_zone_{i}_event`, event_type `triggered`, device_class doorbell | `event.py:47-48`, `64` |
| Botões de pânico | audível(1), silencioso(0), incêndio(2), médico(3); pânico só com senha salva; incêndio/médico só AMT 8000 | `button.py:48-62`, `38`, `56` |
| Botão Desligar Sirene | só centrais (não eletrificador); se disparado envia `DEACTIVATE_CENTRAL` | `button.py:42-44`, `113-118` |
| Switches eletrificador | "Choque" (`{mac}_shock`) e "Alarme" (`{mac}_alarm`) | `switch.py:76-78`, `159-161` |
| Config flow | Passo 1: host+porta; Passo 2: **OAuth via callback URL** (não email/senha) | `config_flow.py:30-35`, `101-146` |
| Options (menu) | unified_alarm, device_password, manage_zones, **reauth** | `config_flow.py:175` |
| Unified config | `home_partitions`, `away_partitions`, `partition_arm_modes` | `const.py:79-82` |
| Versão | `manifest.json` = **1.0.0**; `hacs.json` sem `version`, HA mínimo **2024.1.0** | `manifest.json:11`, `hacs.json` |
| Polling | ISECNet 1s; Cloud ~30s; SSE para eventos | `const.py:13`, `coordinator.py:93`, `102-116` |
| Timeout "triggered" | 120s e **apenas** para órfãos de SSE (não 10 min global) | `coordinator.py:54` |

---

## 1. Inconsistências encontradas

| # | Doc | Trecho na doc | Realidade no código (arquivo:linha) | Severidade | Ação |
|---|-----|---------------|--------------------------------------|------------|------|
| R1 | README.md | Config pede "Email" + "Senha" da conta Intelbras | Flow usa host/porta + OAuth callback URL (`config_flow.py:30-35,101-146`) | **Alta** | Corrigido |
| R2 | README.md | Diagrama/Funcionalidades sem plataforma `event`, sem "Último Disparo" | `const.py:73`, `event.py`, `sensor.py:180-205` | Média | Corrigido (prosa) |
| R3 | README.md | "Botão Desligar Sirene" (só sirene) | 4 botões de pânico + sirene (`button.py:48-62`) | Média | Corrigido |
| R4 | README.md | "timeout automático de 10 min para estados stale" | 120s, só p/ órfão SSE (`coordinator.py:54`) | Baixa | Corrigido (removida afirmação imprecisa) |
| R5 | README.md | Estrutura `home_assistant/custom_components/...`; sem `event.py` | Integração na raiz `custom_components/`; `event.py`/`strings.json`/`translations/` existem | Média | Corrigido |
| R6 | README.md | "Home Assistant 2023.x ou posterior" | `hacs.json` exige `2024.1.0` | Baixa | Corrigido |
| R7 | README.md | Implica que partição individual tem Em Casa/Ausente | Partição individual só expõe `ARM_AWAY` (`alarm_control_panel.py:158`) | Baixa | Corrigido (esclarecimento) |
| I1 | INSTALACAO.md | PASSO 3.3 pede "Email" + "Senha" | host/porta + OAuth callback (`config_flow.py`) | **Alta** | Corrigido |
| I2 | INSTALACAO.md | Lista de arquivos sem `button.py`/`event.py` | Ambos existem | Baixa | Corrigido (+`en.json`) |
| I3 | INSTALACAO.md | "Polling padrao: 30 segundos" | ISECNet 1s + Cloud 30s + SSE | Baixa | Corrigido |
| I4 | INSTALACAO.md | entity_ids de exemplo (`..._particao_1`, `cerca_eletrica`) | `has_entity_name=True`: id deriva do nome do device + entidade; switches são "Choque"/"Alarme" | Baixa | **Pendente** (ilustrativo) |
| P1 | RESUMO_PROJETO.md | Arquivo `api.py` (l.105) | Real: `api_client.py` | Média | **Pendente** (doc histórico) |
| P2 | RESUMO_PROJETO.md | Só entidades alarm/binary_sensor/sensor (l.146-161) | Faltam switch, button, event, alarme unificado | Média | **Pendente** (doc histórico) |
| P3 | RESUMO_PROJETO.md | Config "email e senha" (l.167-168, 331-332) | OAuth | Média | **Pendente** (doc histórico) |
| P4 | RESUMO_PROJETO.md | "Apenas via nuvem" / ISECNet como futuro de longo prazo (l.226-234, 271-275) | ISECNet já implementado (relay cloud; IP receiver local também suportado) | Média | **Pendente** (doc histórico) |
| S1 | IMPLEMENTATION_STATUS.md / FASE1_VALIDATION.md | FASE 2-6 "Not Started"; progresso 16.7% (l.218-227) | Integração HA completa (todas as plataformas) | **Alta** | **Pendente** (decisão) |
| S2 | IMPLEMENTATION_STATUS.md / FASE1_VALIDATION.md / CHANGELOG.md | Diretório `fastapi_middleware/` | Real: `intelbras-guardian-api/` | Média | **Pendente** (relativo à API) |
| S3 | CHANGELOG.md / manifest.json / hacs.json | manifest = `1.0.0`; CHANGELOG topo `[Unreleased]`/`[0.1.0]`; `hacs.json` sem version | Inconsistência de versão; sem entrada 1.0.0 | Média | **Pendente** (decisão) |

---

## 2. Correções aplicadas

### README.md
- **Login (config)**: removidos campos "Email/Senha"; documentado o fluxo real host/porta + OAuth (link no navegador + colar callback URL) e a re-autenticação via Opções.
- **Painel de Controle de Alarme**: esclarecido que **partição individual** só expõe Ausente/Desarmar (Home/Away exige a entidade unificada); documentado o **arme "Ausente" atômico** (não arma nada com zona aberta → notificação acionável); removido o "timeout de 10 min".
- **Botões (Pânico e Sirene)**: documentados os 4 botões de pânico (audível/silencioso/incêndio/médico), gating por senha salva e por família AMT 8000, e o comportamento de `DEACTIVATE_CENTRAL` ao silenciar com a central disparada.
- **Sensores de Evento**: documentados `Last Event`, **`Último Disparo`** e a entidade `event` por zona.
- **Pré-requisitos**: "2023.x" → "2024.1.0 (conforme `hacs.json`)".
- **Estrutura do Projeto**: caminho corrigido para `custom_components/` (raiz); adicionados `event.py`, `strings.json`, `translations/`.

### INSTALACAO.md
- **PASSO 3.3**: removidos "Email/Senha"; documentado host/porta + OAuth callback + re-autenticação.
- **Lista de arquivos**: adicionados `button.py`, `event.py` e `en.json`.
- **"Eventos não atualizam"**: corrigida a descrição de polling (ISECNet 1s, Cloud ~30s, SSE).

> Nenhum arquivo `.py` foi editado. Edições restritas à lista de docs autorizada.

---

## 3. Itens pendentes (precisam de decisão sua/do usuário)

1. **`IMPLEMENTATION_STATUS.md`, `FASE1_VALIDATION.md`, `CHANGELOG.md`** estão presos na "FASE 1 / 16.7%" e descrevem a API por `fastapi_middleware/` (real: `intelbras-guardian-api/`). Reescrevê-los exige decisão de produto (datas, quais fases marcar, notas de release). **Não editados.**
2. **Versão (S3)**: `manifest.json` está em `1.0.0`, mas o `CHANGELOG.md` não tem entrada 1.0.0 (topo em `[Unreleased]`/`[0.1.0]`) e `hacs.json` não traz `version`. Definir: criar entrada `## [1.0.0]` no CHANGELOG e/ou alinhar versões. (Adicionar notas = conteúdo a decidir, não inventei.)
3. **`RESUMO_PROJETO.md`** é um snapshot histórico da análise do APK; várias afirmações ficaram obsoletas (api.py, só 3 entidades, config email/senha, ISECNet "futuro"). Decidir se vira doc atual ou se ganha aviso de "documento histórico". **Não editado.**
4. **INSTALACAO.md – entity_ids de exemplo (I4)**: como `has_entity_name=True`, os IDs reais dependem do nome do dispositivo no HA. Decidir se troca por exemplos genéricos/placeholder.
5. **Diagrama ASCII do README**: a lista de entidades dentro do desenho ainda omite `event`/pânicos (não editei o desenho para não quebrar o alinhamento das bordas). Posso reconstruir o bloco se desejar.

---

## 4. Lacunas de documentação (recursos sem doc)

- ✅ **Sensor "Último Disparo"** (recente, em working tree): era totalmente ausente; **agora documentado** no README.
- ✅ **Arme "Ausente" atômico** (recente, em working tree): comportamento novo; **agora documentado** no README.
- ✅ **Plataforma `event`** (eventos de disparo por zona): era ausente; **agora documentada**.
- ✅ **Botões de pânico** (audível/silencioso/incêndio/médico) e suas regras: eram ausentes; **agora documentados**.
- ⚠️ **Options flow**: passos "Configurar Senha do Dispositivo" e "Gerenciar Nomes de Zonas" existem (`config_flow.py:391-513`) mas têm pouca/nenhuma cobertura nos guias do usuário.
- ⚠️ **Atributos expostos** úteis para automações/template switches não documentados: `pre_trigger_arm_mode`, `last_arm_intent`, `partition_status`, `connection_unavailable_raw` (`alarm_control_panel.py:336-366`, `941-982`).
- ⚠️ **Diagrama ASCII** (README) permanece incompleto quanto a `event`/pânicos (ver pendência 5).
