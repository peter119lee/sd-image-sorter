// Ambient window surface for page.evaluate() callbacks.
// The product frontend is deliberately framework-free vanilla JS with no type
// definitions, so the app globals the e2e suite pokes at are declared as `any`.
//
// `interface Window` merges across every file in the project, so a spec that
// re-declares one of these members with a different shape silently replaces it
// for all the others. A spec needing a precise shape must use a local
// intersection type (`typeof window & { ... }`) instead — see
// guide-pins.spec.ts, reverse-prompt.spec.ts and ai-runtime-busy.spec.ts.
export {}

declare global {
  interface Window {
    App?: any
    Gallery?: any
    UiScale?: any
    ArtistIdent?: any
    CensorEdit?: any
    EntryPage?: any
    V321Integration?: any
    CaptionCore?: any
    // app.js is a classic script, so its top-level functions are globals.
    getSelectedGalleryCount?: any
    showBatchExportModal?: any
    __taggerSystemInfoStatus?: any
  }
}
