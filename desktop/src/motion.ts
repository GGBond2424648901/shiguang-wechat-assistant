// The interruptible WAAPI approach and easing curve follow Salary Glass Clock.
const ease = "cubic-bezier(.22,.8,.25,1)";
const entering = new WeakMap<HTMLElement, Animation>();

export function reducesMotion() {
  return document.documentElement.dataset.reduceMotion === "true"
    || matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function enterRoute(element: HTMLElement, direction: number, reduceMotion: boolean) {
  entering.get(element)?.cancel();
  if (reduceMotion || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const effect = element.animate([
    { opacity: .45, transform: `translate3d(0, ${direction * 5}px, 0)` },
    { opacity: 1, transform: "translate3d(0, 0, 0)" },
  ], { duration: 175, easing: ease });
  entering.set(element, effect);
  const cleanup = () => {
    if (entering.get(element) === effect) entering.delete(element);
  };
  effect.finished.then(cleanup, cleanup);
  return () => effect.cancel();
}
