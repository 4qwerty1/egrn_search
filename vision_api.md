# judge-doc

OCR для PNG и JPEG в существующем n8n: `img to txt` (`HSZVkEXCs93KTQek`) на `n8n.aoken.ru`. Workflow передаёт изображение в подключение `Ollama aoken` с моделью `qwen3-vl:30b` и возвращает текст в JSON. Отдельные n8n и Ollama не нужны.

**Статус:** пользователь опубликовал ревизию `a7a177c7-560c-4490-8272-ad80e0851ee6`. Рабочий webhook проверен на двух PNG (выполнения `555` и `556`: `200`, JSON и `Cache-Control: no-store`). Запросы из среды проверки принимались без авторизации OCR-клиента. Точность распознавания отдельно фиксируется в журнале качества. Для тестов черновика без публикации используйте **Execute workflow** на канве; **Listen for test event** внутри узла может запустить только Webhook.[1][4]

## Контракт API

`POST /webhook/judge-doc/v1/ocr` — рабочий маршрут. Тестовый маршрут — `/webhook-test/judge-doc/v1/ocr`.[4] Допустимые форматы:

- Сырое тело PNG или JPEG с `Content-Type: image/png` либо `image/jpeg`.
- `application/json`: `{"mime_type":"image/png","image":"<стандартный base64 с padding>"}`. В `mime_type` укажите фактический формат PNG/JPEG. Этот способ подходит и для ручного запуска через MCP.[2]

Workflow принимает до **5 МиБ** декодированных данных. n8n или прокси могут принять меньше и вернуть собственную ошибку. Транспортные ограничения целевого сервера отдельно не проверялись. Проверяется сигнатура, но не полная декодируемость. URL и multipart не поддерживаются.

Пример для синтетического `sample.png`, когда n8n слушает тестовое событие:

```bash
curl.exe --fail-with-body --show-error --max-time 120 -X POST -H 'Content-Type: image/png' --data-binary '@sample.png' 'https://n8n.aoken.ru/webhook-test/judge-doc/v1/ocr'
```

```json
{"text":"ДЕЛО 4827\nИТОГ 1250"}
```

Успех — `200` с `text`; `""` означает, что модель не увидела читаемого текста. Ошибка workflow — `{"error":{"code":"…","message":"…"}}`: `400` для пустого или неверного изображения, `413` при превышении размера, `415` для неподдерживаемого типа, `502` при сбое модели, незавершённом или невалидном ответе. Полный контракт — [contracts/openapi.json](contracts/openapi.json).

## Проверки и сопровождение

`Webhook → проверка изображения → Ollama Analyze Image → проверка транскрипции → JSON`. Узел **Ollama Model** использует действие **Analyze Image** с бинарным полем.[3] Конфигурация — [workflows/img-to-txt.json](workflows/img-to-txt.json). В опубликованной ревизии кириллица — подсказка модели. В черновике `695ca3a9-784d-47b2-a7e6-0612d1ec981e` буквы строго из русского алфавита, включая `Ё` и `ё`; цифры, знаки и регистр проходят без нормализации. Если модель возвращает другие буквы, черновик отвечает `502 model_response_invalid` без подмены символов. Пока черновик не опубликован, рабочий URL использует прежнее правило. В файле есть имя подключения без его ключа. `.env` исключён из Git; `N8N_KEY` используется только для MCP и не является ключом OCR-клиента.

```bash
node --test tests/img-to-txt.test.mjs
python -m pip install -r requirements-test.txt
python scripts/smoke.py
```

Smoke запускает черновик в режиме `manual` на синтетических изображениях и сверяет итоговый JSON с OpenAPI. Он проверяет реальную модель и логику до `Return OCR JSON`, но не HTTP-транспорт. Прогоны `536–539`: PNG на кириллице, JPEG на латинице, пустой кадр и неверный base64. Прогоны `540–541` проверили бинарную ветку и отказ при неверной сигнатуре с подставленным ответом модели.

Перед использованием реальных документов согласуйте авторизацию OCR-клиента и хранение данных исполнений. До этого используйте только тестовые изображения. Распознанный текст судебных документов сверяйте с оригиналом.

- [План разработки и приёмки](docs/PLAN.md)
- [Журнал решений](docs/DECISIONS.md)
- [Журнал ошибок](docs/ERRORS.md)
- [Журнал качества распознавания](docs/QUALITY.md)

Ветки: `main` — выпуски, `dev` — интеграция, `feature/ocr-api` — реализация.

## Sources

[1] https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook — n8n: Webhook
[2] https://docs.n8n.io/connect/connect-to-n8n-mcp-server/mcp-server-tools-reference — n8n: MCP server tools reference
[3] https://github.com/n8n-io/n8n/blob/master/packages/%40n8n/nodes-langchain/nodes/vendors/Ollama/actions/image/analyze.operation.ts — n8n: Ollama Analyze Image source
[4] https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/workflow-development — n8n: Test and production webhooks
