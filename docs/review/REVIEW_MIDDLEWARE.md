# Auditoria de Documentação — Middleware FastAPI (intelbras-guardian-api)

Agente: **rev-api** (time docs-audit) · Data: 2026-05-27

Escopo: comparar a documentação (`docs/API.md`, `docs/PROTOCOL.md`/`intelbras-guardian-api/docs/PROTOCOL.md`, `docs/DEPLOYMENT.md`, `docs/TESTING.md`, `README.md` do add-on, `CHANGELOG.md`, `PLANO_INSTALADOR.md`, `TESTE_E_VALIDACAO.md`) contra o código real em `intelbras-guardian-api/app/**/*.py`. Código tratado como fonte de verdade.

Prefixo global do router: `/api/v1` (`app/api/v1/__init__.py:11`). Sub-prefixos: `/auth`, `/devices`, `/alarm`, `/events`, `/zones`.

---

## 1. Tabela de inconsistências

Severidade: **ALTA** = caminho/método/contrato errado (quebra o cliente); **MÉDIA** = campo/erro/comportamento divergente; **BAIXA** = texto de mensagem/exemplo desatualizado.

| # | Doc | Trecho documentado | Realidade no código (arquivo:linha) | Sev |
|---|-----|--------------------|--------------------------------------|-----|
| 1 | API.md | `GET /alarm/{device_id}/status` com query param `password` | É **POST** com body `GetStatusRequest` (`alarm.py:743-747`, modelo `alarm.py:117-122`) | ALTA |
| 2 | API.md | Eletrificador em `/eletrificador/{device_id}/shock/on`, `/shock/off`, `/alarm/activate`, `/alarm/deactivate` | Estão sob `/alarm/{device_id}/eletrificador/...`: `eletrificador/activate` (`alarm.py:1099`), `eletrificador/deactivate` (`alarm.py:1176`), `eletrificador/shock/on` (`alarm.py:1253`), `eletrificador/shock/off` (`alarm.py:1328`) | ALTA |
| 3 | API.md | Zonas em `GET /devices/{id}/zones`, `PUT/DELETE /devices/{id}/zones/{i}/friendly-name` | Router prefix é `/zones`: `GET /zones/{id}` (`zones.py:80`), `PUT/DELETE /zones/{id}/{i}/friendly_name` (`zones.py:178,216`) — e é `friendly_name` (underscore), não `friendly-name` | ALTA |
| 4 | API.md | `GET /devices` retorna array puro `[ {...} ]` | Retorna objeto `DeviceListResponse` = `{ "devices": [...], "total": N }` (`devices.py:57-61,126,163`) | ALTA |
| 5 | API.md | Erro de zonas abertas: `{"detail": "...", "error_code": "open_zones", "open_zones": [0,2,5]}` | `detail` é **objeto**: `{"error": "OpenZonesError", "message": "...", "open_zones": [{index,name,friendly_name}]}` (`alarm.py:437-451, 486-500`) | ALTA |
| 6 | API.md | Tabela de erros lista só 400/401/404/500 | Há também **503** `ConnectionUnavailable` (`alarm.py:469-475, 609-617, 706-713, 1454-1461, 1555-1562`) com `detail` objeto | MÉDIA |
| 7 | API.md | `POST /arm` erro `401: Senha inválida` | Senha do painel incorreta vira `AlarmOperationError` → **400** (`alarm.py:501, 531-532`). `401` é só sessão inválida (`alarm.py:529-530`) | MÉDIA |
| 8 | API.md | `GET /status/auto` erro `404: Nenhuma senha salva` | Sem senha salva → `AlarmOperationError` → **400** (`alarm.py:878, 1033`). `503` quando conexão falha sem cache (`alarm.py:955`) | MÉDIA |
| 9 | API.md | `arm`/`disarm` resposta `{success, new_status}` | Modelo `AlarmOperationResponse` = `{success, device_id, partition_id, new_status, message}` (`alarm.py:65-72, 518-524, 633-639`) | MÉDIA |
| 10 | API.md | `arm` body só `{partition_id, mode, password}` | `ArmRequest` tem também `local_ip`, `local_port`, `save_password`; `partition_id` opcional (None = todas) (`alarm.py:33-40`) | MÉDIA |
| 11 | API.md | `GET /status` resposta sem `device_id/model/mac/message/eletrificador/connection_unavailable/last_updated` e zonas sem campos wireless | `AlarmStatusResponse` (`alarm.py:94-114`) inclui todos; zonas têm `name, is_wireless, battery_low, signal_strength, tamper, is_in_alarm` (`alarm.py:81-91`) | MÉDIA |
| 12 | API.md | `siren/off` sem body; resposta `{success, message:"Sirene desligada"}` | Requer body `EletrificadorRequest` (password); retorna `EletrificadorOperationResponse` `{success, device_id, new_status, message:"Sirene desligada com sucesso"}` (`alarm.py:1403-1407, 1478-1483`) | MÉDIA |
| 13 | API.md | `GET /auth/session` resposta sem `is_valid` | `SessionResponse` inclui `is_valid` e torna `username`/`expires_at` opcionais (`auth.py:60-65`) | MÉDIA |
| 14 | PROTOCOL.md | V1 `PANIC = 0x50 (80, 'P')` | Código: `PANIC = 0x45 (69)`, payload `[69, tipo]` (`isecnet_protocol.py:65`). `0x50`/'P' é o sufixo de modo STAY no arm, não pânico | MÉDIA |
| 15 | API.md | `POST /password` resposta `"Senha salva com sucesso"` | `{"success": True, "message": "Password saved"}` (`devices.py:304`) | BAIXA |
| 16 | API.md | `DELETE /password` resposta `"Senha excluída com sucesso"` | `{"success": True, "message": "Password deleted"}` (`devices.py:330`) | BAIXA |
| 17 | API.md | `DELETE .../friendly-name` resposta `"Nome amigável excluído"` | `{"success": True, "message": "Friendly name removed for zone {i}"}` (`zones.py:234`) | BAIXA |
| 18 | API.md | `GET /zones` resposta `{device_id, zones}` | `ZonesResponse` inclui `total_zones` (`zones.py:31-35`) | BAIXA |
| 19 | API.md | SSE `connected` → `{"message":"Connected to event stream"}` | Código envia `{"client_id":..., "message":"Conectado ao stream de eventos"}` (`events.py:222`) | BAIXA |
| 20 | API.md | `/events` resposta `"total": 150` (sugere total geral) | `total = len(events)` (página atual) (`events.py:162-167`); evento tem `is_read` e `raw_data` (`events.py:36-46`) | BAIXA |
| 21 | API.md | `GET /health` resposta `{"status":"healthy"}` | Retorna também `service, version, config, stats, isecnet` (`main.py:117-131`) | BAIXA |
| 22 | API.md | Login resposta sem `message` | `LoginResponse` inclui `message` (`auth.py:26-30`) | BAIXA |

---

## 2. Correções APLICADAS

Em **docs/API.md**:
- Nota da URL base / health em `/api/v1/health`; endpoints públicos incluem o fluxo OAuth.
- `GET /devices`: corrigido para objeto `{devices, total}`, adicionado `is_online` e campos de zona (`friendly_name, stay_enabled, bypassed`).
- `GET /auth/session`: adicionado `is_valid`; login: adicionado `message`.
- Adicionada seção dos endpoints OAuth (`/auth/start`, `/auth/callback`, `/auth/callback-url`, `/auth/oauth-callback`).
- `POST /password` e `DELETE /password`: mensagens corrigidas; nota sobre memória/`STATE_BACKEND`.
- Adicionados `GET /devices/{id}/password/check` e `GET /devices/{id}/partitions/status`.
- `POST /arm`: body completo, resposta completa, tabela de erros corrigida (400/401/404/503), nota de "uma partição por chamada".
- `POST /disarm`: resposta completa + erros.
- **`GET /status` → `POST /status`** (método e body corrigidos) + resposta completa.
- `GET /status/auto`: erro `400` (não `404`) + comportamento `connection_unavailable`/cache.
- `bypass-zone`: body (`password`/`save_password`) + forma real da resposta.
- `siren/off`: body obrigatório + resposta `EletrificadorOperationResponse`.
- Adicionado `POST /alarm/{id}/panic` e endpoints auxiliares (`/info`, `/disconnect`, `/debug/complete-status`).
- **Eletrificador**: 4 caminhos corrigidos para `/alarm/{id}/eletrificador/...` + respostas reais.
- **Zonas**: prefixo `/zones`, `friendly_name` (underscore), `total_zones`, erro `400` sem senha, mensagens reais.
- Eventos: SSE `connected` real, nota de poll ~5s, `GET /events/stream/stats`, semântica de `total`, campos `is_read`/`raw_data`.
- Tabela de erros: adicionado `503`; novas seções para `ConnectionUnavailable` (503) e `OpenZonesError` (400, estrutura objeto).
- `GET /health`: exemplo de resposta completo.

Em **intelbras-guardian-api/docs/PROTOCOL.md**:
- Comando V1 `PANIC` corrigido de `0x50/80/'P'` para `0x45/69` com nota do payload `[69, tipo]`.

---

## 3. Pendências para decisão (não alteradas — ambíguas)

- **PROTOCOL.md, "Status Estendido Smart (0x5D)"**: a tabela diz "1-8 | 8 bytes | 64 zonas" e "13-18 | zonas anuladas", mas o parser usa 6 bytes/48 zonas para o AMT 2018 E Smart (model 52) e **não** extrai bypass do status V1 (`isecnet_protocol.py:990-997, 1073-1124`; bypass de zona não é setado no parse V1). Offsets de wireless/tamper/bateria (63-68/69-74/81-86/107+) **batem**. Ambíguo se a doc descreve o frame cru (RE) ou o que o código parseia — relatado, não corrigido.
- **`docs/TESTING.md`**: arquivo é um stub ("Coming soon in FASE 2") e não reflete a suíte real (`tests/integration/test_real_api.py`). Decidir se reescrever ou remover.
- **`CHANGELOG.md`** (1.0.0): mínimo; não menciona panic, bypass, siren-off, split shock/alarm do eletrificador, sensores wireless, SSE, fallback `connection_unavailable`. Decidir se expandir.
- **`TESTE_E_VALIDACAO.md`**: usa caminhos ilustrativos do middleware incorretos para o estado atual (`POST /api/login`, `GET /api/devices`, `GET /api/alarm/{id}/status`) — deveriam ser `/api/v1/...`. Como o documento é uma metodologia/template genérico (e mistura endpoints da API upstream da Intelbras), não apliquei correção; recomendo alinhar os exemplos ao prefixo `/api/v1` se for mantido como referência.
- **`EVENT_POLL_INTERVAL`** (`config.py:77-80`, default 30s) **não é usado**: `event_stream.py:37` fixa `self._poll_interval = 5`. Não é erro de doc (a doc nova diz ~5s, correto), mas é divergência código↔config a decidir pela equipe de código.

---

## 4. Lacunas (endpoints/recursos sem doc) — agora documentados em API.md

Estavam ausentes na doc e foram adicionados (ou relatados):
- `POST /auth/start`, `POST /auth/callback`, `POST /auth/callback-url`, `GET /auth/oauth-callback` (`auth.py:119-317`).
- `GET /devices/{id}/password/check` (`devices.py:336`), `GET /devices/{id}/partitions/status` (`devices.py:211`).
- `POST /alarm/{id}/panic` (`alarm.py:1507`), `GET /alarm/{id}/info` (`alarm.py:1044`), `POST /alarm/{id}/disconnect` (`alarm.py:1705`), `GET /alarm/{id}/debug/complete-status` (`alarm.py:1588`).
- `GET /events/stream/stats` (`events.py:306`).

Documentação verificada como **correta** (sem alteração necessária):
- `README.md` (add-on) e `docs/DEPLOYMENT.md`: variáveis de ambiente conferem com `config.py` (`INTELBRAS_API_URL`, `INTELBRAS_OAUTH_URL`, `INTELBRAS_CLIENT_ID`, `HOST`, `PORT`, `DEBUG`, `LOG_LEVEL`, `CORS_ORIGINS`) e com `.env.example`. Caminhos docker (`docker/Dockerfile`, `docker/docker-compose.yml`, serviço `fastapi`, healthcheck em `/api/v1/health`) conferem.
- `config.yaml` do add-on (opção `log_level`) bate com o README.
- PROTOCOL.md: comandos/códigos V2 (CONNECT 0x30F6, APP_CONNECT 0xFFF1, AUTHORIZE 0xF0F0, ALARM_PANEL_STATUS 0x0B4A, BYPASS_ZONE 0x401F, etc.), portas (V2 9009/80, V1 9015), `partition_index` 0xFF=todas, mapeamento de partições V1 (A=0x41…), checksums e bitmask de bypass V1 — todos conferem com `isecnet_protocol.py`.

> Observação de escopo: `README.md` raiz, `INSTALACAO.md` e `custom_components/*` **não** foram tocados (responsabilidade de outro agente).
