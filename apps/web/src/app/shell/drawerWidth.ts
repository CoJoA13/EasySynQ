export const DRAWER_MIN = 360;
export const DRAWER_MAX = 640;
export const DRAWER_DEFAULT = 420;

export function clampDrawerWidth(px: number): number {
  return Math.max(DRAWER_MIN, Math.min(DRAWER_MAX, Math.round(px)));
}
