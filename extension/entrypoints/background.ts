/**
 * Background service worker — prefetches latest data.json on extension startup.
 */

import { loadData } from "../src/lib/data";

export default defineBackground(() => {
  browser.runtime.onInstalled.addListener(async () => {
    console.log("[Bias] Extension installed — prefetching data...");
    try {
      const data = await loadData();
      console.log(
        `[Bias] Data loaded: ${Object.keys(data.journalists).length} journalists, version ${data.version}`,
      );
    } catch (e) {
      console.error("[Bias] Failed to prefetch data:", e);
    }
  });
});
