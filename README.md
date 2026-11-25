# Фид RESURS для Яндекс Директа

Фид собирается автоматически на GitHub, запускать на компьютере ничего не нужно.

## Файлы
- `build_feed.py` собирает фид. Настройки лежат вверху файла: страницы каталога, описание, исключения.
- `title_overrides.csv` хранит ручные заголовки для товаров, которые не помещаются в 56 символов.
- `.github/workflows/build-feed.yml` запускает сборку каждый день в 06:17 по Москве и при каждом изменении скрипта или заголовков.
- `feed.xml` — готовый фид (появится после первого запуска).
- `long_titles.csv` — заголовки длиннее 56 символов, которые ещё не исправлены вручную.

## Первый запуск
1. Создайте публичный репозиторий, например `resurs-feed`.
2. Add file → Upload files: перетащите все файлы из архива вместе с папкой `.github`.
3. Settings → Actions → General → Workflow permissions → **Read and write permissions** → Save.
4. Actions → Build RESURS feed → **Run workflow**.
5. Settings → Pages → Source: Deploy from a branch → `main` / `(root)` → Save.
6. Ссылка на фид: `https://<логин>.github.io/resurs-feed/feed.xml`

## Как исправить длинные заголовки
1. Откройте `long_titles.csv` (или лог запуска в Actions) — там список товаров длиннее 56 символов.
2. Добавьте строку в `title_overrides.csv`: `uid;title_site;title_feed`.
   `uid` можно оставить пустым, тогда товар найдётся по названию с сайта.
3. Сохраните файл (Commit changes) — фид пересоберётся сам.

## Если какой-то раздел не нашёлся
В логе будет «не найден блок магазина». Откройте страницу раздела → Ctrl+U → Ctrl+F `storepart`,
скопируйте число и впишите его в `MANUAL_STOREPARTS` в `build_feed.py`, например:
`"/catalog/sets": ["133001602081"],`
