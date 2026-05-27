# Recomendações de Arquitetura e Prevenção — Intelbras Guardian

> Documento de auditoria (somente análise). Não altera código. Foco: os três bugs
> diagnosticados em maio/2026 no fluxo "armar Ausente com zona aberta" + o histórico
> recorrente de fixes em `unified` e `coordinator`, e como evitar a próxima regressão.
>
> Arquivos centrais analisados:
> - `custom_components/intelbras_guardian/coordinator.py`
> - `custom_components/intelbras_guardian/alarm_control_panel.py`
> - `custom_components/intelbras_guardian/__init__.py`
> - `custom_components/intelbras_guardian/api_client.py`
> - `custom_components/intelbras_guardian/sensor.py`, `const.py`
> - `intelbras-guardian-api/app/api/v1/alarm.py`

---

## 1. Os 3 bugs e o histórico

Os 3 bugs corrigidos hoje (working tree, não commitado):

1. **Estado "Ausente" com 1 partição desarmada** — `_compute_state` declarava o conjunto
   armado bastando **uma** partição (`armed_indices.issubset(target)`).
2. **Arme "Ausente" não-atômico** — o loop armava cada partição em separado; a partição
   sem zona aberta (B) armava de fato (sirene bipa) antes de o usuário decidir o bypass da A.
3. **Notificação de disparo com "Teste periódico"** — o painel ia a `triggered` ~5s antes de
   `last_event` receber a zona; `last_event` é um campo genérico poluído por eventos de rotina.

Estes não são incidentes isolados. O `git log` mostra **uma cadeia longa de fixes no mesmo
núcleo** — todos sintomas da mesma dívida arquitetural:

```
7d628e5 fix(unified): compute pre_trigger_arm_mode from partition snapshot
65aa4fa fix(unified): validate restored intent against actual armed partitions
de48750 fix(unified): persist arming intent across restarts and infer from partition sets
0543741 fix(unified): only clear arming intent when API confirms disarm
1109c52 fix(unified): keep arming intent during transient disarm windows
853290c fix(unified): prefer user-issued arming intent over partition mode heuristics
07e5d20 fix(unified): preserve pre-trigger arm mode and recompute state by partition mode
5f3a1ec fix(coordinator): release stuck triggers when zones settle
90d57de fix: suppress false alarm triggers during arm/disarm transitions
```

Nove dos últimos ~15 commits mexem em `_compute_state`, `_last_arm_intent`, ou no tratamento
de `triggered`/phantom/SSE no coordinator. Isso é a assinatura de **lógica de estado sem
modelo explícito**: cada caso novo vira mais um `if` no topo da pilha de prioridades.

---

## 2. Padrões de causa raiz

### (a) Estado derivado de múltiplas fontes, sem máquina de estados única

O estado exibido para o usuário é o resultado de **quatro fontes que se sobrepõem sem
reconciliação formal**:

| Fonte | Onde escreve | Evidência |
|---|---|---|
| Estado otimista (UI imediata) | `self._optimistic_state` | `alarm_control_panel.py:213-216` (individual), `:904-906` (unified) |
| Polling ISECNet (1 Hz) | reconstrói `self.data` inteiro | `coordinator.py:290-711` |
| SSE (cloud, push) | muta `self.data` direto | `_apply_state_change` `coordinator.py:157-197`; `_apply_alarm_trigger` `:199-288` |
| Intenção do usuário | `self._last_arm_intent` + RestoreEntity | `alarm_control_panel.py:683`, `:694-719` |

Não há nenhum objeto que represente "o estado atual do painel". `state` é **recomputado
sob demanda** toda vez que HA lê a property (`_compute_state`, `alarm_control_panel.py:810-899`),
misturando os quatro insumos em uma pilha de `if`. Como cada fonte chega em momento
diferente, qualquer ordem de chegada inesperada produz um estado transitório errado — e a
correção vira mais um caso especial (ex.: `de48750`, `1109c52`, `0543741` são todos sobre
**quando** confiar/limpar `_last_arm_intent`).

### (b) Heurísticas frágeis de classificação em vez de modelo explícito

Em vez de mapear estados do painel → estados HA por uma tabela única, o código adivinha:

- Casamento por substring de string: `"AWAY" in status_upper or "ARMED" in status_upper`
  (`alarm_control_panel.py:254-257` e duplicado em `_get_real_state` `:327-331`). Note que
  `"disarmed"` **contém** `"armed"` — armadilha já documentada num comentário do próprio
  arme home (`:1162-1163`).
- Prefixo `str(...).startswith("armed")` espalhado pelo coordinator (`coordinator.py:184,
  191, 235, 242, 451, 462, 473`).
- Pilha de prioridades de 5 níveis em `_compute_state` (`:816-899`): triggered → bypass
  pendente → desarmado → intent → topologia → contagem de modos. O **Bug 1** foi exatamente
  um nível dessa pilha usar `issubset` no sentido errado (`armed ⊆ target` em vez de
  `target ⊆ armed`; comentário em `:856-862`).
- Lógica duplicada: `_compute_state` e `_compute_pre_trigger_arm_mode` (`:911-939`) reimplementam
  a mesma recuperação por topologia com pequenas diferenças — duas cópias que precisam ser
  mantidas em sincronia à mão.

### (c) Operações multi-partição não-atômicas

- O contrato do servidor é **uma partição por chamada**: `ArmRequest.partition_id: Optional[int]`
  (`alarm.py:35`), endpoint `POST /{device_id}/arm` (`alarm.py:329-368`).
- O cliente loopa: `_execute_arm_away` chama `arm_partition` para cada índice
  (`alarm_control_panel.py:1276-1296`); `_execute_arm_home` idem (`:1176-1196`). Não há
  rollback: se a partição A falha por zona aberta (400 `OpenZonesError`) **depois** de B já
  ter sido armada, B fica armada (sirene) e o estado fica inconsistente (**Bug 2**).
- O patch atual (pré-check `_get_live_open_zones`, `:1248-1269`) **mitiga** o caso "zona aberta
  conhecida antes de armar", mas não torna a operação transacional: uma falha de rede no meio
  do loop (`:1294-1296`) ainda deixa partições parcialmente armadas, e o `_optimistic_state = None`
  do erro (`:1302-1303`) não desarma o que já armou.

### (d) Descompasso temporal entre fontes

- **triggered vs last_event**: o painel vai a `triggered` ~5s antes de `last_event` receber a
  zona (prova na memória do projeto: `09:44:17 triggered` / `09:44:22 last_event=CAM 4 PISCINA`).
  Mitigado capturando `_last_trigger` **síncrono** com `is_triggered` a partir da zona
  `is_in_alarm` (`coordinator.py:482-504`) e expondo `GuardianLastTriggerSensor` (`sensor.py`,
  novo). Mas o problema de fundo — automações lendo um campo "último de qualquer coisa" no
  instante de uma transição — continua latente para qualquer outro consumidor de `last_event`.
- **Timezones**: o coordinator emite UTC (`datetime.now(timezone.utc).isoformat()`,
  `coordinator.py:282` e `:502`); `sensor.py:160-162` faz `fromisoformat(ts.replace("Z","+00:00"))`;
  a API REST do HA retorna timestamps em **UTC** mas interpreta parâmetros sem tz como **hora
  local** (nota de topologia). Não há um único ponto que normalize tz — cada borda decide.
- **Janelas mágicas espalhadas**: `_triggered_timeout = 120` (`coordinator.py:54`),
  `_phantom_trigger_grace = 90` (`:76`), `_connection_unavailable_grace = 60` (`:87`),
  `_sse_triggered_until = now + 30` (`:253`), `_BYPASS_STALE_TIMEOUT = 300` (`__init__.py:18`),
  `timeout=15` do optimistic clear (`alarm_control_panel.py:266, 984`). São acoplamentos
  temporais implícitos: mudar um sem o outro reabre bugs (ex.: `1109c52` e `7b3d1b3` foram
  ajustes de janela).

### (e) Contratos de dados frouxos (dicts não-tipados)

- `api_client` devolve `Dict[str, Any]` cru e o resto do código faz `.get("success")`,
  `.get("open_zones")`, `.get("error")` (`api_client.py:232-282`, `:284-338`). Um typo numa
  chave é silencioso (retorna `None`/`False`), não um erro.
- O coordinator monta um dict gigante sem schema (`coordinator.py:700-711`) com chaves
  "privadas" por convenção (`_zone_index`, `_partition_index`, `_last_trigger`).
- As entidades acessam dezenas de chaves por string: `partition.get("status")`,
  `device.get("is_triggered")`, `device.get("real_time_status")`, `rt.get("is_armed")`
  (`alarm_control_panel.py:224-232`, `coordinator.py:1045`). O servidor **tem** modelos
  Pydantic ricos (`AlarmStatusResponse`, `PartitionStatusInfo`, `alarm.py:75-114`) — mas o
  contrato é **perdido na fronteira**: `api_client` re-serializa para dict e o coordinator
  reconstrói tudo à mão. Inconsistências como `state` (servidor) vs `status` (partição no
  coordinator, `:437`) vivem por causa disso.

### (f) Ausência de testes automatizados para a lógica de estado

- Único teste existente: `intelbras-guardian-api/tests/integration/test_real_api.py` — bate na
  **API real** (não roda em CI, exige central física).
- **Zero** testes unitários para `_compute_state`, `_handle_coordinator_update`, ou a captura
  de `_last_trigger` — justamente onde os 3 bugs e os ~9 fixes recentes aconteceram.
- CI (`.github/workflows/build.yaml`) só **builda a imagem Docker do add-on** e dispara apenas
  em mudanças sob `intelbras-guardian-api/**`. O `custom_components/**` (onde estão os bugs)
  **não tem lint, nem pytest, nem hassfest** em nenhum workflow. `validate-hacs.yaml` valida só
  metadados HACS.

---

## 3. Recomendações concretas e priorizadas

### P0 — #1 Suíte pytest para `_compute_state` (e captura de trigger)

- **Problema que previne:** regressões na máquina de classificação de estado — exatamente a
  classe dos Bugs 1 e 3 e dos ~9 fixes de `unified`/`coordinator`. Hoje cada fix é validado
  "no olho" via logs de produção do HA do usuário.
- **Por que primeiro:** maior ROI e menor risco. Não exige refatorar nada; só extrai a lógica
  para algo testável e fixa o comportamento esperado **antes** de mexer na arquitetura. Vira a
  rede de segurança das recomendações seguintes.
- **Esboço de implementação:**
  - `_compute_state` já é quase puro: depende de `_get_partition_states()`, `_active_bypass()`,
    `_last_arm_intent`, `_home_partitions`/`_away_partitions`, e `device.is_triggered`. Extrair
    o miolo para uma **função pura** `compute_unified_state(intent, partition_states,
    away_set, home_set, bypass_arm_type, is_triggered) -> AlarmState` (sem `self`/`hass`).
  - `tests/components/test_unified_state.py` com `pytest`, parametrizado pela tabela da §4.
  - Cobrir também `coordinator._last_trigger`: dado um snapshot de zonas com `is_in_alarm`,
    o `zone_name` capturado é o da zona em alarme (não o `last_event` de rotina).
  - Mockar HA com `pytest-homeassistant-custom-component` apenas no nível necessário; a função
    pura não precisa de hass.
- **Bugs que teria evitado:** **Bug 1** (caso `intent=away, armadas={0}, away={0,1} → ARMING`,
  nunca `ARMED_AWAY`) seria um caso vermelho da tabela. Também `853290c`, `07e5d20`, `65aa4fa`.

### P0 — #2 Máquina de estados explícita para o painel unificado

- **Problema que previne:** a pilha de `if` ad-hoc e a falta de um estado `ARMING` "de
  verdade". Hoje `ARMING` só existe como `_optimistic_state` temporário
  (`alarm_control_panel.py:1147, 1242`) ou como efeito colateral de `_active_bypass()`
  (`:869-875`); não é um estado de primeira classe com entrada/saída definidas.
- **Esboço de implementação:**
  - Definir estados: `DISARMED`, `ARMING`, `ARMED_HOME`, `ARMED_AWAY`, `PENDING_BYPASS`
    (arme aguardando decisão de zona aberta), `TRIGGERED`, `DISARMING`, `UNAVAILABLE`.
  - Transições explícitas e nomeadas, ex.:
    - `DISARMED --arm_away--> ARMING`
    - `ARMING --all_target_partitions_armed--> ARMED_AWAY`
    - `ARMING --open_zone_detected--> PENDING_BYPASS`
    - `PENDING_BYPASS --bypass_confirmed--> ARMING`
    - `PENDING_BYPASS --timeout(_BYPASS_STALE_TIMEOUT)--> DISARMED`
    - `* --zone_in_alarm--> TRIGGERED`; `TRIGGERED --disarm--> DISARMED`.
  - `ARMING` só "completa" quando `target ⊆ armed` (regra do Bug 1 vira **invariante** da
    transição, não um `if` perdido).
  - Encapsular `_last_arm_intent`, `pending_bypass` e `_optimistic_state` como **dados de
    contexto da máquina**, não atributos soltos do entity. O `state` property passa a ser
    `return self._machine.state` — sem recomputo heurístico.
  - Unificar `_compute_state` e `_compute_pre_trigger_arm_mode` (`:911-939`): ambos derivam
    home/away da topologia → uma única função, reusada pela máquina.
- **Bugs que teria evitado:** Bug 1 e toda a família de fixes de "quando limpar/preservar
  intent" (`0543741`, `1109c52`, `de48750`) — essas viram regras de transição testáveis, não
  remendos no `_handle_coordinator_update` (`:1008-1048`).

### P1 — #3 Operação de arme atômica (servidor + cliente)

- **Problema que previne:** **Bug 2** na raiz (partição B arma antes de a A ser confirmada) e
  estados parciais por falha no meio do loop.
- **Esboço de implementação:**
  - Servidor: novo contrato multi-partição. Ou um endpoint `POST /{device_id}/arm` que aceite
    `partitions: list[int]` + `mode`, faça o pré-check de zonas abertas de **todas** as
    partições-alvo numa conexão ISECNet, e só então arme — retornando `OpenZonesError` com as
    zonas de todo o conjunto **sem ter armado nada** (estende `ArmRequest`, `alarm.py:33-39`;
    e o bloco verify, `alarm.py:393-456`, que já sabe checar `arm_mode`/zonas abertas).
  - Cliente: `_execute_arm_away` chama uma vez (`alarm_control_panel.py:1276-1296` vira uma
    chamada), eliminando o loop e o pré-check duplicado em `_get_live_open_zones` (`:783-808`).
  - Semântica "tudo-ou-nada": se não puder armar todas, não arma nenhuma; a máquina de estados
    (#2) vai para `PENDING_BYPASS`.
  - Onde o painel físico não suportar arme atômico real, manter o pré-check de zonas como
    porta de entrada (já feito) e tornar a transição `ARMING → ARMED_*` condicionada a
    `target ⊆ armed` **confirmado por status**, com rollback explícito no erro.
- **Bugs que teria evitado:** Bug 2; e o risco latente de falha parcial em `:1294-1303`.

### P1 — #4 Tipagem/validação dos contratos entre `api_client` e coordinator

- **Problema que previne:** chaves silenciosamente ausentes/erradas (família (e)); mismatch
  `state` vs `status`; quebra invisível quando o servidor muda um campo.
- **Esboço de implementação:**
  - Reusar/espelhar os modelos Pydantic do servidor (`AlarmStatusResponse`,
    `PartitionStatusInfo`, `ZoneStatusInfo`, `alarm.py:75-114`) como **`@dataclass` ou
    `TypedDict`** no cliente (HA evita dependência de pydantic v1/v2 no core; dataclass +
    `from_dict` é o caminho mais leve). Ex.: `AlarmStatus`, `Partition`, `Zone`, `ArmResult`.
  - `api_client.get_alarm_status_auto` e `arm_partition` retornam dataclasses tipadas, não
    `Dict[str, Any]` (`api_client.py:220-282`).
  - Coordinator passa a operar sobre objetos tipados; o dict de saída
    (`coordinator.py:700-711`) ainda pode existir para HA, mas construído a partir dos tipos.
  - `mypy`/`ruff` no CI (#7) pega os `.get("typo")` que hoje passam.
- **Bugs que teria evitado:** classe de regressões "campo renomeado no servidor"; e clareza
  para o Bug 3 (o `_last_trigger` seria um tipo com `zone_name` garantido, não `dict.get`).

### P2 — #5 Única fonte de verdade + reconciliação idempotente

- **Problema que previne:** o "tira-e-põe" entre optimistic, SSE e polling — origem dos fixes
  `0543741`/`1109c52`/`de48750` e do caso 2026-05-04 documentado em `:1024-1029` (uma partição
  some do payload por 1 ciclo e o estado pula para Ausente).
- **Esboço de implementação:**
  - Definir o **estado canônico** = última leitura confirmada do painel (`real_time_status`).
    SSE e optimistic são **camadas de predição** com TTL, nunca a verdade.
  - `reduce(canonical_state, event) -> canonical_state` idempotente: aplicar a mesma leitura
    duas vezes não muda nada; aplicar SSE e depois polling converge para o mesmo resultado.
  - Optimistic vira "overlay" com expiração explícita (já existe o relógio em
    `_schedule_optimistic_clear`, `:984-992`) e regra única de descarte ("limpa quando a
    leitura canônica concorda OU TTL expira") — substitui as três variações espalhadas de
    "quando confiar" em `_handle_coordinator_update` (`:1008-1048`).
  - Tratar payload parcial como "desconhecido", **nunca** como "desarmado" (a lição de
    `:1024-1046`, hoje resolvida com `rt.get("is_armed") is False` — formalizar como regra).
- **Bugs que teria evitado:** `0543741`, `1109c52`, `de48750`, `65aa4fa` (todos sobre quando o
  estado derivado pode/deve mudar).

### P2 — #6 Padronização de timezone

- **Problema que previne:** ambiguidade UTC vs local nos timestamps de trigger/evento e nas
  consultas à API do HA (família (d)).
- **Esboço de implementação:**
  - Regra única: **tudo internamente em UTC aware** (`datetime.now(timezone.utc)`, já usado em
    `coordinator.py:282, 502`); converter para local **só na borda de exibição** via
    `homeassistant.util.dt` (`dt_util.utcnow()`, `dt_util.as_local()`).
  - Centralizar parsing num helper (substitui o `replace("Z","+00:00")` ad-hoc de
    `sensor.py:160-162`).
  - Para sensores de timestamp, usar `SensorDeviceClass.TIMESTAMP` com `datetime` aware (HA
    formata no fuso do usuário) em vez de string ISO crua.
- **Bugs que teria evitado:** confusões de horário em notificações/automações; alinha com a
  nota de topologia de que a API REST do HA mistura UTC (retorno) e local (parâmetros).

### P2 — #7 CI mínimo para `custom_components/**`

- **Problema que previne:** hoje nenhum gate automático cobre o código onde os bugs vivem
  (`build.yaml` só toca `intelbras-guardian-api/**`).
- **Esboço de implementação:** workflow `.github/workflows/ci.yaml` com, em `push`/`pull_request`
  tocando `custom_components/**`:
  - `ruff check` + `ruff format --check` (lint/estilo);
  - `pytest` (suíte da #1) com `pytest-homeassistant-custom-component`;
  - `home-assistant/actions/hassfest` (valida manifest/estrutura da integração);
  - opcional: `mypy` nos módulos tipados da #4.
- **Bugs que teria evitado:** não evita um bug específico, mas converte cada um dos anteriores
  num **teste que roda em todo PR** — interrompe a recorrência.

---

## 4. Tabela de casos de teste para `_compute_state`

Topologia real do usuário (memória do projeto): **Em Casa = {A=0}**, **Ausente = {A=0, B=1}**,
ambas em modo Total. Logo `home_set = {0}`, `away_set = {0,1}`. Estados de partição usam os
rótulos do parser: `disarmed`, `armed_away`, `armed_stay`/`armed_home`, `armed`.

Entrada → estado esperado (assinatura proposta:
`compute(intent, partitions, away_set={0,1}, home_set={0}, bypass_arm_type, is_triggered)`):

| # | intent | partições (idx→status) | bypass pendente | is_triggered | **Esperado** | Cobre |
|---|--------|------------------------|-----------------|--------------|--------------|-------|
| 1 | `away` | `{0:armed_away, 1:disarmed}` | — | F | **ARMING** | **Bug 1** (não pode ser ARMED_AWAY com B desarmada) |
| 2 | `away` | `{0:armed_away, 1:armed_away}` | — | F | **ARMED_AWAY** | caminho feliz away |
| 3 | `away` | `{0:disarmed, 1:disarmed}` | `away` | F | **ARMING** | **Bug 2** (held aguardando bypass, nada armado) |
| 4 | `away` | `{0:disarmed, 1:disarmed}` | — | F | **DISARMED** | nada armado, sem bypass |
| 5 | `home` | `{0:armed_stay, 1:disarmed}` | — | F | **ARMED_HOME** | caminho feliz home (home_set={0} completo) |
| 6 | `home` | `{0:armed_away, 1:disarmed}` | — | F | **ARMED_HOME** | intent home vence o modo da partição (`853290c`) |
| 7 | `None` | `{0:armed_away, 1:armed_away}` | — | F | **ARMED_AWAY** | restart sem intent → recupera por topologia (away_set) |
| 8 | `None` | `{0:armed_stay, 1:disarmed}` | — | F | **ARMED_HOME** | restart → recupera home pela topologia |
| 9 | `away` | `{0:armed_away, 1:armed_away}` | — | **T** | **TRIGGERED** | triggered tem prioridade máxima |
| 10 | `away` | `{0:armed_away, 1:disarmed}` | `away` | F | **ARMING** | bypass pendente + away incompleto |
| 11 | `home` | `{0:disarmed, 1:disarmed}` | `home` | F | **ARMING** | held no arme home com zona aberta |
| 12 | `None` | `{0:armed_away, 1:disarmed}` | — | F | **ARMED_HOME** | padrão = home_set exato → home (não away parcial) |
| 13 | `away` (restaurado, contradiz) | `{0:armed_stay, 1:disarmed}` | — | F | **ARMED_HOME** | **65aa4fa**: intent restaurado inválido é ignorado, cai na topologia |
| 14 | `None` | `{0:armed, 1:armed}` (sem sufixo) | — | F | **ARMED_AWAY** | `armed` genérico → usa `partition_arm_modes` (default away), conjunto = away_set |
| 15 | `away` | `{0:disarmed, 1:armed_away}` | — | F | **ARMED_AWAY** | parcial fora dos sets exatos → fallback por contagem de modo (`mode_counts.away>0`) |

> Observação para o caso 14/15: a função pura deve receber `partition_arm_modes` para
> resolver o `armed` genérico (hoje `alarm_control_panel.py:847-852`). A tabela acima é o
> contrato de regressão; transformá-la em `@pytest.mark.parametrize` é a entrega da #1.

---

## 5. Sequenciamento sugerido

1. **#1 (testes de `_compute_state`)** — primeiro, fixa o comportamento atual já corrigido.
2. **#7 (CI)** — roda #1 em todo PR.
3. **#2 (máquina de estados)** — refatora com a rede de #1 ativa.
4. **#3 (arme atômico)** e **#4 (tipos)** — em paralelo, em worktrees separados.
5. **#5 (fonte única/reconciliação)** e **#6 (timezone)** — endurecimento final.
</content>
</invoke>
