# Bounded Mandate — app

The client for [Bounded Mandate](../README.md). Expo / React Native, iOS 26,
Liquid Glass.

It is a thread: you say what you want, the agent shops, and the engine's verdict
lands in the conversation as a card.

The app holds **no policy, no Razorpay key, and no ElevenLabs key**. It renders
verdicts it did not compute and cannot appeal. Every credential lives in the
engine's server process, and the phone talks only to that host — including for
voice, which round-trips audio through the engine rather than going to
ElevenLabs directly.

## Running it

The engine must be up first; the app finds it at the Expo dev-server host on
port 8117, so the simulator and a device on the same LAN both work unconfigured.

```bash
# from the repository root
set -a; . ./.env; set +a
uv run uvicorn bounded_mandate.web:app --host 0.0.0.0 --port 8117

# here
npx expo start
```

Override the engine's address with `EXPO_PUBLIC_API_BASE` if it lives elsewhere.

## Layout

```
src/
  app/           expo-router routes — _layout.tsx and the thread
  components/    bubble, decision-card
  lib/           api.ts (the engine), voice.ts (mic and playback)
  theme/         native semantic colours, one colour per verdict
```
