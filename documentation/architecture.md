# Architecture

The browser application calls the versioned `/api` path, proxied by Vite in development. FastAPI handles request validation, dependency-injected SQLAlchemy sessions, JWT user resolution, and ownership filters. PostgreSQL is supported through `DATABASE_URL`; SQLite provides a portable local default. Models are normalized around users, categories, transactions, budgets, insights, and savings goals.

Analytics aggregate the signed-in user's stored transaction rows. The forecast summarizes expenses by calendar month and fits a linear trend when three or more months exist. The categorizer trains from the current user's labeled transaction descriptions when there are enough examples and multiple labels; otherwise it uses a small interpretable keyword baseline. Its result includes the method and confidence so the UI can retain manual control.
