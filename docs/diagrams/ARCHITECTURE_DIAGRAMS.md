# Intelbras Guardian — Diagramas de Arquitetura e Fluxos

> Diagramas em Mermaid **validados contra o código real** (incluindo mudanças
> ainda não commitadas: pré-check atômico de arme em `alarm_control_panel.py`
> e o sensor `_last_trigger` em `coordinator.py`/`sensor.py`). Cada diagrama traz
> referências a `arquivo:linha` que comprovam o fluxo.
>
> Caminhos relativos à raiz do projeto:
> - `cc/` = `custom_components/intelbras_guardian/`
> - `api/` = `intelbras-guardian-api/app/`

---

## 1. Arquitetura de componentes

O Home Assistant cria entidades por plataforma (`alarm_control_panel`, `sensor`,
`event`, etc. — `cc/const.py:73`), todas `CoordinatorEntity` ligadas ao
`GuardianCoordinator` (`cc/coordinator.py:21`). O coordinator fala com o
middleware via `GuardianApiClient` (`cc/api_client.py:12`), que faz HTTP REST
contra a FastAPI (`api/main.py:58`, router em `api/api/v1/alarm.py:24`). O
serviço `isecnet_client` abre a conexão ISECNet com a central AMT, escolhendo
entre **Cloud relay** ou **IP Receiver** conforme `_get_device_connection_info`
(`api/api/v1/alarm.py:153`, decisão em `:186-207`). Em paralelo, o canal **SSE**:
`event_stream` (`api/services/event_stream.py:22`) faz polling da nuvem
(`_poll_loop` `:123`) e também recebe `broadcast_event` direto dos comandos
arm/disarm (`api/api/v1/alarm.py:510`,`:625`); o endpoint `/events/stream`
(`api/api/v1/events.py:244`) entrega os eventos ao `listen_sse_events`
(`cc/api_client.py:448`), que chama `coordinator._on_sse_event`
(`cc/coordinator.py:137`).

```mermaid
graph TD
    subgraph HA["Home Assistant — custom_components/intelbras_guardian"]
        ACP["alarm_control_panel<br/>Unified + Individual<br/>(alarm_control_panel.py)"]
        SEN["sensor<br/>LastEvent / ÚltimoDisparo / Signal<br/>(sensor.py)"]
        EVT["event<br/>ZoneEvent (event.py)"]
        OTHER["binary_sensor / switch / button"]
        COORD["GuardianCoordinator<br/>DataUpdateCoordinator<br/>(coordinator.py:21)"]
        APICL["GuardianApiClient<br/>(api_client.py:12)"]
        ACP --> COORD
        SEN --> COORD
        EVT --> COORD
        OTHER --> COORD
        COORD -->|"get_alarm_status_auto<br/>get_devices / get_events"| APICL
    end

    subgraph MW["Middleware FastAPI — intelbras-guardian-api/app"]
        ROUTER["routers /api/v1<br/>alarm.py · events.py · devices.py"]
        ISEC["isecnet_client<br/>(services/isecnet_client.py)"]
        STREAM["event_stream<br/>_poll_loop + broadcast_event<br/>(services/event_stream.py:22)"]
        STATE["state_manager<br/>(senhas, cache, conn_info)"]
        AUTH["auth_service / guardian_client<br/>(OAuth + Cloud REST)"]
        ROUTER --> ISEC
        ROUTER --> STATE
        ROUTER -->|"broadcast_event<br/>state_changed"| STREAM
        STREAM --> AUTH
    end

    subgraph CLOUD["Intelbras"]
        RELAY["Cloud relay<br/>(is_cloud_enabled)"]
        IPRX["IP Receiver server<br/>(is_ip_receiver_server_enabled)"]
        CLOUDAPI["Cloud REST API<br/>(devices / events)"]
    end

    CENTRAL["Central AMT<br/>ISECNet V2<br/>(AMT_2018_E_SMART etc.)"]

    APICL -->|"HTTP REST<br/>X-Session-ID"| ROUTER
    APICL -.->|"GET /events/stream (SSE)"| STREAM
    STREAM -.->|"event_generator → queue<br/>(events.py:212)"| APICL
    APICL -.->|"_on_sse_event<br/>(coordinator.py:137)"| COORD

    ISEC -->|"ISECNet via cloud"| RELAY
    ISEC -->|"ISECNet direto"| IPRX
    AUTH --> CLOUDAPI
    RELAY --> CENTRAL
    IPRX --> CENTRAL
    CLOUDAPI -.->|"eventos<br/>(last_event)"| AUTH
```

---

## 2. Sequência — Armar "Ausente" com zona aberta (comportamento ATÔMICO pós-fix)

Ao tocar **Ausente**, `async_alarm_arm_away` grava `_last_arm_intent="away"` e
otimisticamente vai para `ARMING` (`cc/alarm_control_panel.py:1235-1243`). Antes
de armar qualquer partição, `_execute_arm_away` faz um **pré-check ao vivo** das
zonas abertas via `_get_live_open_zones` (`:1257`, que chama
`get_alarm_status_auto`). Se houver zona aberta, **NADA é armado** (evita disparar
a sirene numa partição enquanto a outra fica pendente): registra o bypass
pendente com `_store_bypass_and_notify` (`:1266`), limpa o estado otimista e
retorna — o `_compute_state` então reporta **ARMING** porque há bypass pendente e
o conjunto-alvo não está completo (`:869-875`). A notificação acionável "Ignorar
Zonas e Armar" é enviada ao mobile (`cc/__init__.py:27`). Quando o usuário
confirma, o evento `mobile_app_notification_action` cai em
`_handle_notification_action` (`cc/__init__.py:260`) → `_execute_bypass_and_rearm`
(`cc/__init__.py:96`): faz `bypass_zones` (`:155`), aguarda 0.5s, marca
`entity._skip_open_zone_check=True` (`:176`) e re-chama `async_alarm_arm_away()`
(`:179`). Dessa vez o pré-check é pulado (`:1254-1256`), todas as partições do
away set são armadas → **ARMED_AWAY**.

```mermaid
sequenceDiagram
    actor User
    participant Panel as GuardianUnifiedAlarmControlPanel<br/>(alarm_control_panel.py)
    participant API as GuardianApiClient
    participant MW as FastAPI /alarm
    participant Central as Central AMT
    participant Mobile as mobile_app (notificação)

    User->>Panel: tocar "Ausente" (arm_away)
    Note over Panel: _last_arm_intent="away"<br/>optimistic = ARMING (:1242)
    Panel->>Panel: _skip_open_zone_check? (:1254) → False
    Panel->>API: _get_live_open_zones() (:1257)
    API->>MW: GET /alarm/{id}/status/auto
    MW->>Central: ISECNet get_status
    Central-->>MW: zonas (is_open=true)
    MW-->>API: status + zones
    API-->>Panel: [zona aberta]
    Note over Panel: NÃO arma nada (atômico)<br/>_store_bypass_and_notify("away") (:1266)<br/>optimistic = None (:1267)
    Panel->>Mobile: "Ignorar Zonas e Armar"<br/>(__init__.py:_send_bypass_notification)
    Note over Panel: _compute_state → ARMING<br/>(pending bypass + away incompleto, :869-875)

    User->>Mobile: confirmar "Ignorar e Armar"
    Mobile->>Panel: event mobile_app_notification_action<br/>→ _handle_notification_action (__init__.py:260)
    Panel->>Panel: _execute_bypass_and_rearm (__init__.py:96)
    Panel->>API: bypass_zones(zone_indices) (:155)
    API->>MW: POST /alarm/{id}/bypass-zone
    MW->>Central: ISECNet bypass (anular)
    Central-->>MW: ok
    Note over Panel: sleep 0.5s · entity._skip_open_zone_check=True (:176)
    Panel->>Panel: async_alarm_arm_away() (:179) — re-arma
    Note over Panel: skip_check=True → pula pré-check (:1254)
    loop cada partição do away_set
        Panel->>API: arm_partition(idx, mode)
        API->>MW: POST /alarm/{id}/arm
        MW->>Central: ISECNet arm
        Central-->>MW: armed
    end
    Note over Panel: optimistic = ARMED_AWAY (:1299)
    MW-->>API: SSE state_changed (armed_away)
    API-->>Panel: _on_sse_event → ARMED_AWAY confirmado
```

---

## 3. Sequência — Disparo → notificação correta (sensor `Último Disparo`)

Quando a central dispara, o coordinator detecta `is_triggered` por **dois
caminhos**: (a) polling ISECNet — `get_alarm_status_auto` traz `is_triggered` e as
zonas com `is_in_alarm` (`api/api/v1/alarm.py:811` mapeia `triggered`→`is_in_alarm`);
ou (b) SSE — evento `is_alarm` cai em `_apply_alarm_trigger`
(`cc/coordinator.py:199`). No caminho de polling, assim que `is_triggered` é
verdadeiro **e** há zonas, o coordinator captura **sincronamente** a zona em
alarme em `_last_trigger` (`cc/coordinator.py:490-504`), priorizando zonas com
`is_in_alarm`. Esse dicionário é exposto em `data["_last_trigger"]` (`:710`) e
lido pelo sensor `GuardianLastTriggerSensor` (`cc/sensor.py:180`, `_trigger()`
`:207-210`), que atualiza junto com o estado `triggered`. **Problema antigo que
isso resolve:** o sensor `Último Evento` (`GuardianLastEventSensor`,
`cc/sensor.py:93`) usa `last_event`, que é constantemente sobrescrito por
eventos de rotina (ex.: "Teste periódico" da nuvem a cada ~hora), então uma
automação disparada em `triggered` que lia `last_event` via de regra via o
evento errado por ~5s até o próximo poll. O `_last_trigger` só é atualizado por
um disparo real e nunca por eventos de rotina.

```mermaid
sequenceDiagram
    participant Central as Central AMT
    participant MW as FastAPI /alarm + event_stream
    participant Coord as GuardianCoordinator
    participant Trig as sensor.*_last_trigger<br/>(GuardianLastTriggerSensor)
    participant Evt as sensor.*_last_event<br/>(GuardianLastEventSensor)
    participant Auto as Automação HA

    Central-->>MW: zona em alarme (is_in_alarm)
    alt Caminho A — polling ISECNet (a cada ~1s)
        Coord->>MW: GET /alarm/{id}/status/auto
        MW->>Central: ISECNet get_status
        Central-->>MW: is_triggered + zones[is_in_alarm]
        MW-->>Coord: status (triggered → is_in_alarm)
        Note over Coord: is_triggered=True E status_zones<br/>captura SÍNCRONA da zona (:490-504)<br/>_last_trigger[device]={zone_name, zones, ...}
    else Caminho B — SSE (evento is_alarm)
        MW-->>Coord: alarm_event is_alarm=true
        Note over Coord: _apply_alarm_trigger (:199)<br/>is_triggered=True imediato<br/>_last_trigger preenchido se houver zone (:275-285)
    end

    Coord->>Coord: data["_last_trigger"]=... (:710)<br/>async_set_updated_data
    Coord-->>Trig: estado TRIGGERED + zona real (sincronizados)
    Coord-->>Evt: last_event (pode ser "Teste periódico" — desatualizado)
    Trig-->>Auto: lê zone_name correta (que disparou)
    Note over Evt,Auto: PROBLEMA antigo: automação lia last_event<br/>e via "Teste periódico" por ~5s.<br/>Resolvido lendo sensor.*_last_trigger.
```

---

## 4. Máquina de estados do painel unificado

A função `_compute_state` (`cc/alarm_control_panel.py:810`) define a verdade do
estado, com prioridade explícita: (1) `TRIGGERED` quando o device está disparado;
(2) `ARMING` enquanto há **bypass pendente** e o conjunto-alvo ainda não está
totalmente armado (`:869-875`) — é o estado mantido enquanto a zona aberta
aguarda a confirmação "Ignorar e Armar"; (3) `DISARMED` se nenhuma partição
armada; (4) `ARMED_HOME`/`ARMED_AWAY` **somente quando TODAS as partições do
conjunto-alvo estão armadas** (`target ⊆ armed`, via `issubset` em `:861-862`,
`:881-884`) — uma única partição armada já não declara o conjunto inteiro armado;
(5) fallback por topologia/contagem de modos. O `_last_arm_intent` ("home"/"away")
é persistido entre restarts via `RestoreEntity` (`async_added_to_hass` `:694`) e
limpo quando a API confirma `is_armed=False` (`_handle_coordinator_update`
`:1033-1046`) ou ao desarmar (`:1074`).

```mermaid
stateDiagram-v2
    [*] --> DISARMED

    DISARMED --> ARMING: arm_home / arm_away<br/>(optimistic, :1147/:1242)

    ARMING --> ARMING: zona aberta → bypass pendente<br/>conjunto-alvo incompleto (:869-875)
    ARMING --> ARMED_HOME: home_set ⊆ armed<br/>(home_complete, :862/:881)
    ARMING --> ARMED_AWAY: away_set ⊆ armed<br/>(away_complete, :861/:883)
    ARMING --> DISARMED: usuário desarma /<br/>bypass expira sem confirmar

    ARMED_HOME --> TRIGGERED: is_triggered (:832)
    ARMED_AWAY --> TRIGGERED: is_triggered (:832)
    ARMED_HOME --> DISARMED: disarm (:1070)
    ARMED_AWAY --> DISARMED: disarm (:1070)
    ARMED_HOME --> ARMED_AWAY: re-arme (mudança de conjunto)
    ARMED_AWAY --> ARMED_HOME: re-arme (mudança de conjunto)

    TRIGGERED --> DISARMED: disarm /<br/>phantom-trigger release (coordinator.py:630)
    TRIGGERED --> ARMED_AWAY: re-arme durante disparo<br/>(pre_trigger_arm_mode)
    TRIGGERED --> ARMED_HOME: re-arme durante disparo

    note right of ARMING
      ARMING é mantido enquanto
      _active_bypass() != None e o
      conjunto-alvo NÃO está completo.
      Regra ARMED_*: target ⊆ armed_indices
      (issubset), não armed ⊆ target.
    end note
```

---

## 5. Fontes de verdade / fluxo de dados

Três fontes alimentam o estado, com cadências diferentes. **Polling ISECNet:**
`_async_update_data` roda a cada `DEFAULT_SCAN_INTERVAL=1s` (`cc/const.py:13`);
chama `get_alarm_status_auto` **todo ciclo** (estado em tempo real:
arm_mode/is_triggered/zonas), mas faz throttle das chamadas de nuvem
(`get_devices`/`get_events`) para a cada ~30s (`cloud_api_interval`,
`cc/coordinator.py:92-96`,`:310-336`). **SSE:** eventos `state_changed`
(emitidos pelos próprios comandos arm/disarm, `api/api/v1/alarm.py:510`,`:625`)
→ `_apply_state_change` (`cc/coordinator.py:157`) atualiza partição/arm_mode
direto no cache; eventos `is_alarm` → `_apply_alarm_trigger`
(`cc/coordinator.py:199`) marca triggered imediato. **Eventos da nuvem:**
`event_stream._poll_loop` (`api/services/event_stream.py:123`) faz polling da
REST a cada 5s e vira `last_event` / sensor `Último Evento`. **Risco de
descompasso:** (a) `last_event` (nuvem) reflete o último evento de QUALQUER tipo,
incluindo "Teste periódico" — por isso o `_last_trigger` síncrono do diagrama 3;
(b) triggers só por SSE que a API nunca confirma ("SSE-orphan") são liberados por
timeout (`_triggered_timeout=120s`, `:644-660`); (c) trigger "fantasma" latcheado
pela central mesmo sem zona em alarme é liberado após `_phantom_trigger_grace=90s`
(`:619-643`); (d) durante disparo a AMT_2018_E_SMART zera o byte de partição, daí
o snapshot `_pre_trigger_arm_mode`/`_pre_trigger_partition_status` (`:442-474`).

```mermaid
graph LR
    subgraph SRC["Fontes de verdade"]
        POLL["Polling ISECNet<br/>get_alarm_status_auto<br/>(todo ciclo ~1s)"]
        CLOUDT["Cloud REST (throttle ~30s)<br/>get_devices / get_events"]
        SSE_SC["SSE state_changed<br/>(comando arm/disarm)"]
        SSE_AL["SSE is_alarm<br/>(evento de disparo)"]
        CLOUDEV["Cloud events poll (~5s)<br/>event_stream._poll_loop"]
    end

    subgraph COORD["GuardianCoordinator (cache .data)"]
        ARM["arm_mode / is_armed / is_triggered<br/>+ partitions[].status"]
        ZONES["zones[] (is_open / is_in_alarm)"]
        LASTEV["last_event"]
        LASTTRG["_last_trigger (síncrono)"]
        PRETRG["_pre_trigger_arm_mode / _partition_status"]
    end

    POLL -->|":375-474 atualiza estado real"| ARM
    POLL -->|":476-562 zonas"| ZONES
    POLL -->|":490-504 captura síncrona"| LASTTRG
    POLL -->|":442-474 snapshot durante disparo"| PRETRG
    CLOUDT -->|":332 events[0]"| LASTEV
    SSE_SC -->|"_apply_state_change :157"| ARM
    SSE_AL -->|"_apply_alarm_trigger :199"| ARM
    SSE_AL -->|":275-285 se houver zone"| LASTTRG
    CLOUDEV -->|"vira last_event"| LASTEV

    ARM --> STATE["Estado das entidades<br/>(alarm_control_panel / sensores)"]
    ZONES --> STATE
    LASTTRG --> STATE
    LASTEV --> STATE
    PRETRG --> STATE

    RISK["⚠ Descompasso:<br/>• last_event sobrescrito por 'Teste periódico'<br/>• SSE-orphan trigger (timeout 120s, :644)<br/>• phantom trigger latcheado (grace 90s, :619)<br/>• byte de partição zerado no disparo → snapshot"]:::risk
    LASTEV -.-> RISK
    SSE_AL -.-> RISK
    PRETRG -.-> RISK

    classDef risk fill:#fff3cd,stroke:#d39e00,color:#5a4500;
```
