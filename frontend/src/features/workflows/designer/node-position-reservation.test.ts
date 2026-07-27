import assert from "node:assert/strict";
import test from "node:test";

import {
  releaseCanvasNodePosition,
  reserveCanvasNodePosition,
  type CanvasPositionReservation,
} from "./node-position-reservation.ts";

const committed = [
  { position: { x: 80, y: 140 } },
  { position: { x: 400, y: 140 } },
];

test("rapid allocations reserve different stable positions before either server command finishes", () => {
  const first = reserveCanvasNodePosition(committed, [], "pending-1");
  const second = reserveCanvasNodePosition(committed, first.reservations, "pending-2");

  assert.deepEqual(first.reservation.position, { x: 720, y: 140 });
  assert.deepEqual(second.reservation.position, { x: 80, y: 390 });
  assert.deepEqual(first.reservation.position, { x: 720, y: 140 });
});

test("a repeated drop coordinate is relocated while the first allocation is pending", () => {
  const first = reserveCanvasNodePosition([], [], "pending-1", { x: 220, y: 260 });
  const second = reserveCanvasNodePosition([], first.reservations, "pending-2", { x: 220, y: 260 });

  assert.deepEqual(first.reservation.position, { x: 220, y: 260 });
  assert.notDeepEqual(second.reservation.position, first.reservation.position);
});

test("failure releases only its reservation so the slot can be reused without moving other pending nodes", () => {
  const first = reserveCanvasNodePosition(committed, [], "pending-1");
  const second = reserveCanvasNodePosition(committed, first.reservations, "pending-2");
  const remaining = releaseCanvasNodePosition(second.reservations, "pending-1");
  const third = reserveCanvasNodePosition(committed, remaining, "pending-3");

  assert.deepEqual(remaining, [second.reservation]);
  assert.deepEqual(third.reservation.position, first.reservation.position);
  assert.deepEqual(second.reservation.position, { x: 80, y: 390 });
});

test("refreshing committed nodes does not mutate an existing pending reservation", () => {
  const first = reserveCanvasNodePosition(committed, [], "pending-1");
  const refreshedCommitted = committed.map((node, index) => ({
    position: { x: node.position.x + index * 10, y: node.position.y + index * 20 },
  }));
  const reservations: CanvasPositionReservation[] = [first.reservation];
  reserveCanvasNodePosition(refreshedCommitted, reservations, "pending-2");

  assert.deepEqual(first.reservation.position, { x: 720, y: 140 });
});
