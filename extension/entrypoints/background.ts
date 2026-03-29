/**
 * Background service worker — prefetches latest data.json on extension startup.
 */

import { loadData } from "../src/lib/data";

export default defineBackground(() => {
  chrome.runtime.onInstalled.addListener(async () => {
    console.log("[Byline Card] Extension installed — prefetching data...");
    try {
      const data = await loadData();
      console.log(
        `[Byline Card] Data loaded: ${Object.keys(data.journalists).length} journalists, version ${data.version}`,
      );
    } catch (e) {
      console.error("[Byline Card] Failed to prefetch data:", e);
    }
  });
});
