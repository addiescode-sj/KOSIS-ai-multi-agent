// The motion features LazyMotion loads asynchronously (T3-64): animations, variants, exit, hover/tap/focus.
// Imported only through loadFeatures in ./features.tsx, so it is a chunk of its own, not in the first screen's JS.
import { domAnimation } from "motion/react";

export default domAnimation;
