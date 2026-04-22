## Local Development

CodeTalks host-run defaults on this machine are:

- frontend: `http://localhost:3005`
- backend API: `http://localhost:8000`
- tool containers: `6070 / 7100 / 8001 / 8080`

Do not point this frontend at `3004`. That port belongs to the Cat Cafe runtime API on this shared machine.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Backend

```bash
cd backend
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The backend loads repo-root `/.env` plus `backend/.env.local`, so host-run overrides like `JOERN_BASE_URL=http://localhost:8080` remain effective.

### Browser Contract

Frontend code falls back to `http://localhost:8000` when `NEXT_PUBLIC_API_URL` is unset, and WebSocket code falls back to `ws://localhost:8000`.

If you override them, they must still point at the CodeTalks backend, not Cat Cafe:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

### Verification

```bash
lsof -nP -iTCP -sTCP:LISTEN | rg ':(3005|8000|6070|7100|8001|8080)\b'
```

Expected listeners:

- `node` on `3005` from `/Volumes/Media/codetalk/frontend`
- backend on `8000`
- Joern on `8080`

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
