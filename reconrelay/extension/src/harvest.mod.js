/*
 * ES-module bridge for harvest.js.
 *
 * MV3 forbids eval/new Function in extension contexts, so the service worker
 * cannot eval the UMD script the way a content script loads it. Importing the
 * plain script as a module still runs it (it assigns to `self`), and this file
 * re-exports the result so module code gets the same single implementation.
 */
import "./harvest.js";

const lib = self.RRHarvest;

export default lib;
export const {
  fromHtml,
  fromScript,
  findSecrets,
  extractHostnames,
  fromCloudPatterns,
  interestingHeaders,
  looksLikeApi,
  hostOf,
  sameSite,
  absolutise,
  dedupe,
} = lib;
