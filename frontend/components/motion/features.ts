import type { FeatureBundle } from "motion/react";

// Motion's animation features (domAnimation) as an async chunk (T3-62): not in the first screen's JS. A new
// question starts the download (preloadMotionFeatures), so they are there by the time its answer card enters.
let bundle: FeatureBundle | null = null;
let loading: Promise<FeatureBundle | null> | null = null;

export function preloadMotionFeatures(): Promise<FeatureBundle | null> {
  loading ??= import("./dom-animation")
    .then((r) => (bundle = r.default))
    .catch(() => {
      loading = null; // try again next time; meanwhile cards just show without the entrance
      return null;
    });
  return loading;
}

// The features if they have arrived. Given to LazyMotion as a plain bundle (not a loader), so nothing changes
// after mount: an async LazyMotion that resolves during hydration makes the streamed page client-render.
export function loadedMotionFeatures(): FeatureBundle | null {
  return bundle;
}
