# Documentação da API FastAPI

Este documento descreve todos os endpoints REST expostos pelo middleware FastAPI.

## URL Base

```
http://localhost:8000/api/v1
```

## Autenticação

Todos os endpoints exceto `/health` e os de autenticação inicial (`/auth/login`, `/auth/start`, `/auth/callback`, `/auth/callback-url`, `/auth/oauth-callback`) requerem autenticação via session ID.

Inclua o session ID no header da requisição:
```
X-Session-ID: seu-session-id
```

> **Nota sobre a URL base**: o router principal usa o prefixo `/api/v1` (definido em `app/api/v1/__init__.py`). O endpoint de health vive em `/api/v1/health` (registrado diretamente em `app/main.py`).

---

## Health Check

### GET /health

Verifica se a API está rodando. Caminho completo: `GET /api/v1/health`.

**Resposta:** (inclui também `service`, `version`, `config`, `stats` e `isecnet`)
```json
{
  "status": "healthy",
  "service": "intelbras-guardian-middleware",
  "version": "1.0.0",
  "config": { "api_url": "...", "state_backend": "memory", "debug_mode": false },
  "stats": { },
  "isecnet": { "active_connections": 0, "connections": {} }
}
```

---

## Endpoints de Autenticação

### POST /auth/login

Autentica com credenciais Intelbras.

**Body da Requisição:**
```json
{
  "username": "email@exemplo.com",
  "password": "sua-senha"
}
```

**Resposta:**
```json
{
  "session_id": "uuid-session-id",
  "expires_at": "2024-01-25T12:00:00Z",
  "message": "Login successful"
}
```

**Erros:**
- `401`: Credenciais inválidas

---

### Endpoints OAuth 2.0 (PKCE)

Além do login por senha (`/auth/login`), a API expõe o fluxo OAuth PKCE (recomendado):

- `POST /auth/start` — inicia o fluxo. Aceita query param opcional `redirect_uri`. Retorna `{ auth_url, state, redirect_uri, instructions }`. Abra `auth_url` no navegador.
- `POST /auth/callback` — completa o login. Body: `{ "code": "...", "state": "...", "redirect_uri": "..."(opcional) }`. Retorna o mesmo formato de `LoginResponse`.
- `POST /auth/callback-url` — alternativa: cole a URL completa do redirect. Body: `{ "callback_url": "...", "redirect_uri": "..."(opcional) }`.
- `GET /auth/oauth-callback?code=...&state=...` — endpoint de redirect (retorna uma página HTML). Chamado automaticamente pelo navegador após o login.

---

### POST /auth/logout

Invalida a sessão atual.

**Headers:**
- `X-Session-ID`: Seu session ID

**Resposta:**
```json
{
  "message": "Logout realizado com sucesso"
}
```

---

### GET /auth/session

Obtém informações da sessão atual.

**Headers:**
- `X-Session-ID`: Seu session ID

**Resposta:**
```json
{
  "session_id": "uuid-session-id",
  "username": "email@exemplo.com",
  "expires_at": "2024-01-25T12:00:00Z",
  "is_valid": true
}
```

---

## Endpoints de Dispositivos

### GET /devices

Lista todas as centrais de alarme associadas à conta.

**Headers:**
- `X-Session-ID`: Seu session ID

**Resposta:** (objeto com `devices` e `total` — NÃO é um array puro)
```json
{
  "devices": [
    {
      "id": 12345,
      "description": "Casa",
      "mac": "AA:BB:CC:DD:EE:FF",
      "model": "AMT 2018",
      "is_online": true,
      "has_saved_password": true,
      "partitions_enabled": false,
      "partitions": [
        {
          "id": 0,
          "name": "Alarme",
          "status": "disarmed",
          "is_in_alarm": false
        }
      ],
      "zones": [
        {
          "id": 1,
          "name": "Zona 01",
          "friendly_name": null,
          "status": "INACTIVE",
          "stay_enabled": true,
          "bypassed": false
        }
      ]
    }
  ],
  "total": 1
}
```

---

### GET /devices/{device_id}

Obtém detalhes de um dispositivo específico.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Resposta:**
Igual ao dispositivo individual na resposta de `/devices`.

---

## Gerenciamento de Senha

### POST /devices/{device_id}/password

Salva a senha do dispositivo para funcionalidade de auto-sync.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo"
}
```

**Resposta:**
```json
{
  "success": true,
  "message": "Password saved"
}
```

**Notas:**
- A senha é armazenada em memória (perdida ao reiniciar o container quando `STATE_BACKEND=memory`)
- Necessária para status em tempo real via ISECNet

---

### DELETE /devices/{device_id}/password

Exclui a senha salva do dispositivo.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Resposta:**
```json
{
  "success": true,
  "message": "Password deleted"
}
```

---

### GET /devices/{device_id}/password/check

Verifica se há senha salva para um dispositivo (não retorna a senha).

**Headers:**
- `X-Session-ID`: Seu session ID

**Resposta:**
```json
{
  "device_id": 12345,
  "has_saved_password": true
}
```

---

### GET /devices/{device_id}/partitions/status

Obtém o status de todas as partições via API cloud (faz uma chamada por partição). Não usa ISECNet.

**Headers:**
- `X-Session-ID`: Seu session ID

**Resposta:**
```json
{
  "device_id": 12345,
  "partitions": [
    {
      "partition_id": 1589800,
      "name": "Partição A",
      "status": "disarmed",
      "is_in_alarm": false,
      "raw": { }
    }
  ]
}
```

---

## Endpoints de Controle de Alarme

### POST /alarm/{device_id}/arm

Arma **uma** partição. Esta chamada arma uma única partição por requisição (não é uma operação atômica multi-partição). Para armar várias partições, faça uma chamada por partição.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "partition_id": 0,
  "mode": "away",
  "password": "senha-do-dispositivo",
  "local_ip": null,
  "local_port": null,
  "save_password": false
}
```

**Campos:**
- `partition_id` (opcional): ID da partição. `null`/omitido = todas as partições (omite o byte de partição no protocolo).
- `mode`: `away` (padrão) ou `home`.
- `password` (opcional, 4-6 dígitos): se omitido, usa a senha salva.
- `local_ip`, `local_port` (opcionais): conexão direta ao painel.
- `save_password` (opcional, padrão `false`): salva a senha informada para uso futuro.

**Opções de Modo:**
- `away`: Arma todas as zonas (total)
- `home`: Arma apenas perímetro (stay/parcial)

**Resposta:**
```json
{
  "success": true,
  "device_id": 12345,
  "partition_id": 0,
  "new_status": "armed_away",
  "message": "Armed in away mode"
}
```

**Erros:**
- `400`: Zonas abertas impedem o arme — retorna `detail.error = "OpenZonesError"` com a lista de zonas abertas (ver seção "Erro de Zonas Abertas"). Também `400` para senha do painel incorreta e demais falhas de operação (`AlarmOperationError`).
- `401`: Sessão inválida/expirada (`X-Session-ID`).
- `404`: Dispositivo não encontrado / sem info de conexão.
- `503`: Conexão com a central indisponível (`detail.error = "ConnectionUnavailable"`).

---

### POST /alarm/{device_id}/disarm

Desarma uma partição.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "partition_id": 0,
  "password": "senha-do-dispositivo",
  "save_password": false
}
```

**Resposta:**
```json
{
  "success": true,
  "device_id": 12345,
  "partition_id": 0,
  "new_status": "disarmed",
  "message": "Disarmed successfully"
}
```

**Erros:**
- `400`: Falha de operação (senha incorreta etc.)
- `401`: Sessão inválida
- `404`: Dispositivo não encontrado
- `503`: Conexão com a central indisponível (`detail.error = "ConnectionUnavailable"`)

---

### POST /alarm/{device_id}/status

Obtém status do alarme em tempo real via protocolo ISECNet.

> **Atenção**: este endpoint é **POST** (com body JSON), não GET. A senha vai no corpo da requisição.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo",
  "local_ip": null,
  "local_port": null,
  "save_password": false
}
```
- `password` é opcional se houver senha salva.

**Resposta:**
```json
{
  "device_id": 12345,
  "model": "AMT_2018_E_SMART",
  "mac": "AA:BB:CC:DD:EE:FF",
  "is_armed": false,
  "arm_mode": "disarmed",
  "is_triggered": false,
  "partitions": [
    { "index": 0, "state": "disarmed" }
  ],
  "partitions_enabled": false,
  "zones": [
    {
      "index": 0,
      "name": "Zona 01",
      "is_open": false,
      "is_bypassed": false,
      "is_wireless": false,
      "battery_low": false,
      "signal_strength": null,
      "tamper": false,
      "is_in_alarm": false
    }
  ],
  "message": "Status retrieved successfully",
  "is_eletrificador": false,
  "shock_enabled": false,
  "shock_triggered": false,
  "alarm_enabled": false,
  "alarm_triggered": false,
  "connection_unavailable": false,
  "last_updated": null
}
```

---

### GET /alarm/{device_id}/status/auto

Obtém status em tempo real usando a senha salva (endpoint de auto-sync). Mantém a conexão ISECNet viva entre chamadas.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Resposta:**
Mesmo formato de `POST /alarm/{device_id}/status`. Se a conexão com o painel falhar, retorna o último estado conhecido com `connection_unavailable: true` e `last_updated` preenchido (em vez de erro).

**Erros:**
- `400`: Nenhuma senha salva para o dispositivo (`AlarmOperationError`)
- `503`: Falha de conexão **sem** estado em cache disponível

---

### POST /alarm/{device_id}/bypass-zone

Bypass (anular) ou remover bypass de zonas.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "zone_indices": [33, 35],
  "bypass": true,
  "password": "senha-do-dispositivo",
  "save_password": false
}
```

**Campos:**
- `zone_indices`: Lista de índices de zonas (base 0) para bypass
- `bypass`: `true` para anular, `false` para remover anulação
- `password` (opcional, 4-6 dígitos): usa a senha salva se omitido
- `save_password` (opcional)

**Resposta:** (modelo `AlarmOperationResponse`; `message` vem do cliente ISECNet)
```json
{
  "success": true,
  "device_id": 12345,
  "partition_id": null,
  "new_status": null,
  "message": "Zones bypassed"
}
```

**Erros:**
- `400`: Bypass negado (sem permissão no painel, central armada) — `AlarmOperationError`
- `503`: Central indisponível (`detail.error = "ConnectionUnavailable"`)

**Notas:**
- V1 (AMT 2018 E Smart, etc.): envia bitmask com todas as zonas de uma vez. É um bitmask de estado completo — zonas não listadas terão bypass removido.
- V2 (AMT 8000, etc.): envia um comando por zona individualmente.

---

### POST /alarm/{device_id}/siren/off

Desliga a sirene sem alterar o estado de arme.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:** (modelo `EletrificadorRequest`)
```json
{
  "password": "senha-do-dispositivo",
  "save_password": false
}
```
- `password` opcional se houver senha salva.

**Resposta:** (modelo `EletrificadorOperationResponse`; `new_status` reflete o modo de arme atual)
```json
{
  "success": true,
  "device_id": 12345,
  "new_status": "armed_away",
  "message": "Sirene desligada com sucesso"
}
```

---

### POST /alarm/{device_id}/panic

Dispara um alarme de pânico no dispositivo.

**Headers:**
- `X-Session-ID`: Seu session ID

**Body da Requisição:**
```json
{
  "panic_type": 1,
  "password": "senha-do-dispositivo",
  "save_password": false
}
```
- `panic_type`: `0` silencioso, `1` audível (padrão), `2` incêndio (família AMT 8000), `3` médico (família AMT 8000).

**Resposta:** `EletrificadorOperationResponse` com `new_status: "triggered"`.

---

### Endpoints auxiliares de alarme

- `GET /alarm/{device_id}/info` — info do dispositivo via API cloud (descrição, mac, model, partitions, zones). Não retorna status em tempo real.
- `POST /alarm/{device_id}/disconnect` — fecha a conexão ISECNet com o painel. Retorna `{ success, device_id, message }`.
- `GET /alarm/{device_id}/debug/complete-status` — endpoint de debug: retorna os bytes brutos (hex) do status completo, com anotações de posição. Útil para análise do protocolo.

---

## Endpoints de Zonas

> **Atenção**: o router de zonas usa o prefixo `/zones` (não `/devices/.../zones`) e o segmento `friendly_name` usa **underscore** (não `friendly-name`).

### GET /zones/{device_id}

Obtém todas as zonas com seus nomes amigáveis. Requer senha salva para o dispositivo.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Resposta:**
```json
{
  "device_id": 12345,
  "total_zones": 48,
  "zones": [
    {
      "index": 0,
      "name": "Zona 01",
      "friendly_name": "Porta da Frente",
      "is_open": false,
      "is_bypassed": false
    },
    {
      "index": 1,
      "name": "Zona 02",
      "friendly_name": null,
      "is_open": true,
      "is_bypassed": false
    }
  ]
}
```

**Erros:**
- `400`: Nenhuma senha salva para o dispositivo

---

### PUT /zones/{device_id}/{zone_index}/friendly_name

Define um nome amigável para uma zona.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)
- `zone_index`: Índice da zona (base 0)

**Body da Requisição:**
```json
{
  "friendly_name": "Porta da Frente"
}
```

**Resposta:**
```json
{
  "success": true,
  "device_id": 12345,
  "zone_index": 0,
  "friendly_name": "Porta da Frente"
}
```

---

### DELETE /zones/{device_id}/{zone_index}/friendly_name

Exclui o nome amigável de uma zona.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)
- `zone_index`: Índice da zona (base 0)

**Resposta:**
```json
{
  "success": true,
  "message": "Friendly name removed for zone 0"
}
```

---

## Endpoints de Eventos

### GET /events

Obtém histórico de eventos do alarme.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Query:**
- `limit`: Número máximo de eventos (padrão: 50, máx: 100)
- `offset`: Offset de paginação (padrão: 0)
- `since`: Data ISO 8601 para filtrar eventos (opcional)

**Resposta:**
```json
{
  "events": [
    {
      "id": 123456,
      "timestamp": "2024-01-24T10:30:00Z",
      "event_type": "alarm_triggered",
      "device_id": 12345,
      "partition_id": 0,
      "zone": {
        "id": 1,
        "name": "Zona 01",
        "friendly_name": "Porta da Frente"
      },
      "notification": {
        "code": 1000,
        "title": "Alarme Disparado",
        "message": "Zona 01 foi disparada"
      },
      "is_read": false,
      "raw_data": { }
    }
  ],
  "total": 50,
  "offset": 0,
  "limit": 50
}
```

> **Nota**: `total` é a quantidade de eventos **retornados nesta página** (`len(events)`), não o total geral disponível. Cada evento também inclui `is_read` e `raw_data` (dados brutos da API, para debug).

---

### GET /events/recent

Obtém os eventos mais recentes.

**Parâmetros de Query:**
- `count`: Número de eventos (padrão: 10, máx: 50)

**Resposta:**
```json
{
  "events": [...],
  "count": 10
}
```

---

### GET /events/stream

Stream de eventos em tempo real via Server-Sent Events (SSE).

**Parâmetros de Query (alternativo ao header):**
- `session_id`: Session ID

**Formato dos eventos SSE:**
```
event: connected
data: {"client_id": "uuid", "message": "Conectado ao stream de eventos"}

event: alarm_event
data: {"event_type": "arm", "device_id": 12345, ...}

event: ping
data: {"timestamp": "2024-01-24T10:30:00Z"}
```

- O evento `ping` é enviado a cada 30s como keepalive
- Os eventos são buscados na Intelbras a cada ~5s (intervalo fixo no `event_stream`)
- A conexão é mantida aberta indefinidamente

---

### GET /events/stream/stats

Estatísticas do stream de eventos (clientes conectados, status do polling).

**Headers:**
- `X-Session-ID`: Seu session ID

**Resposta:** inclui `is_polling`, `poll_interval_seconds`, contagem de clientes, etc.

---

## Endpoints de Eletrificador (Cerca Elétrica)

> **Atenção**: todos os endpoints de eletrificador ficam sob o prefixo `/alarm/{device_id}/eletrificador/...`. A função SHOCK (cerca/choque) e a função ALARM são controladas de forma independente. Todas as respostas seguem o modelo `EletrificadorOperationResponse` (`success`, `device_id`, `new_status`, `message`).

### POST /alarm/{device_id}/eletrificador/shock/on

Liga o choque da cerca (cerca energizada).

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo",
  "save_password": false
}
```

**Resposta:**
```json
{
  "success": true,
  "device_id": 12345,
  "new_status": "shock_on",
  "message": "Choque LIGADO com sucesso"
}
```

---

### POST /alarm/{device_id}/eletrificador/shock/off

Desliga o choque da cerca.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo",
  "save_password": false
}
```

**Resposta:**
```json
{
  "success": true,
  "device_id": 12345,
  "new_status": "shock_off",
  "message": "Choque DESLIGADO com sucesso"
}
```

---

### POST /alarm/{device_id}/eletrificador/activate

Arma o ALARME da cerca (função independente do choque).

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo",
  "save_password": false
}
```

**Resposta:**
```json
{
  "success": true,
  "device_id": 12345,
  "new_status": "alarm_armed",
  "message": "Alarme do eletrificador ARMADO com sucesso"
}
```

---

### POST /alarm/{device_id}/eletrificador/deactivate

Desarma o ALARME da cerca.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo",
  "save_password": false
}
```

**Resposta:**
```json
{
  "success": true,
  "device_id": 12345,
  "new_status": "alarm_disarmed",
  "message": "Alarme do eletrificador DESARMADO com sucesso"
}
```

---

## Respostas de Erro

Todos os erros seguem este formato:

```json
{
  "detail": "Descrição da mensagem de erro"
}
```

### Códigos de Status HTTP Comuns

| Código | Descrição |
|--------|-----------|
| 400    | Bad Request - Parâmetros inválidos, falha de operação (`AlarmOperationError`), senha do painel incorreta, ou zonas abertas |
| 401    | Unauthorized - Sessão (`X-Session-ID`) inválida/expirada, ou credenciais de login inválidas |
| 404    | Not Found - Recurso/dispositivo não existe |
| 503    | Service Unavailable - Conexão com a central indisponível (`detail.error = "ConnectionUnavailable"`) |
| 500    | Internal Server Error |

> **Nota**: a maioria dos erros retorna `{"detail": "mensagem"}`, mas alguns retornam `detail` como **objeto** (ver `OpenZonesError` e `ConnectionUnavailable`).

### Erro de Conexão Indisponível (503)

```json
{
  "detail": {
    "error": "ConnectionUnavailable",
    "message": "Conexao com a central indisponivel: <motivo>. Verifique se o app AMT nao esta aberto."
  }
}
```

### Erro de Zonas Abertas (400)

Quando o arme falha porque o painel permanece desarmado com zonas abertas (erro ISECNet 0xE4), o campo `detail` é um **objeto** com a chave `error` (não `error_code`) e uma lista de **objetos** de zona (não inteiros):

```json
{
  "detail": {
    "error": "OpenZonesError",
    "message": "Não é possível armar: existem zonas abertas",
    "open_zones": [
      { "index": 0, "name": "Zona 01", "friendly_name": "Porta da Frente" },
      { "index": 2, "name": "Zona 03", "friendly_name": null }
    ]
  }
}
```

---

## Documentação Interativa

Quando o middleware FastAPI estiver rodando, acesse:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
