export interface CanvasPosition {
  x: number;
  y: number;
}

export interface CanvasPositionReservation {
  id: string;
  position: CanvasPosition;
}

interface PositionedItem {
  position: CanvasPosition;
}

const NODE_COLUMNS = [80, 400, 720];
const NODE_ROWS = [140, 390, 640, 890, 1140];

function positionIsFree(position: CanvasPosition, occupied: readonly PositionedItem[]): boolean {
  return occupied.every((item) =>
    Math.abs(item.position.x - position.x) >= 250 ||
    Math.abs(item.position.y - position.y) >= 180,
  );
}

export function nextAvailableCanvasNodePosition(
  occupied: readonly PositionedItem[],
): CanvasPosition {
  for (const y of NODE_ROWS) {
    for (const x of NODE_COLUMNS) {
      const candidate = { x, y };
      if (positionIsFree(candidate, occupied)) return candidate;
    }
  }
  return {
    x: 80 + occupied.length * 40,
    y: 140 + occupied.length * 210,
  };
}

export function reserveCanvasNodePosition(
  committedNodes: readonly PositionedItem[],
  reservations: readonly CanvasPositionReservation[],
  id: string,
  preferredPosition?: CanvasPosition,
): { reservation: CanvasPositionReservation; reservations: CanvasPositionReservation[] } {
  const occupied = [...committedNodes, ...reservations];
  const reservation = {
    id,
    position: preferredPosition && positionIsFree(preferredPosition, occupied)
      ? preferredPosition
      : nextAvailableCanvasNodePosition(occupied),
  };
  return { reservation, reservations: [...reservations, reservation] };
}

export function releaseCanvasNodePosition(
  reservations: readonly CanvasPositionReservation[],
  id: string,
): CanvasPositionReservation[] {
  return reservations.filter((reservation) => reservation.id !== id);
}
