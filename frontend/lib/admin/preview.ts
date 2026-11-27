// The admin preview (T3-63) shows sample numbers: it exists in `next dev` only. A production build answers 404
// for its pages and its API, so the samples never reach the deployed site (where /admin is the T3-57 admin app).
export const adminPreviewOn = () => process.env.NODE_ENV !== "production";
