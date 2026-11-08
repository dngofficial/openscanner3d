// Helpers for AI object-selection prompts; kept DOM-free so they can be tested in Node.

export function promptPoint(offsetX, offsetY, width, height, notObject = false) {
  const clamp = value => Math.min(1, Math.max(0, Math.round(value * 10000) / 10000));
  return [clamp(offsetX / width), clamp(offsetY / height), notObject ? 0 : 1];
}

// Prompts on frames that are no longer selected are dropped, because the server only segments selected frames.
export function segmentRequest(order, selected, prompts) {
  const chosen = order.filter(name => selected.has(name));
  const usable = chosen.filter(name => prompts.get(name)?.length)
    .map(frame => ({frame, points: prompts.get(frame)}));
  return usable.some(prompt => prompt.points.some(point => point[2] === 1))
    ? {type: 'segment', selected: chosen, prompts: usable}
    : null;
}
