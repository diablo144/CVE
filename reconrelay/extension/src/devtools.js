/*
 * DevTools integration: adds a "ReconRelay" panel next to Network/Console.
 * devtools.html is only allowed to create panels, so all real work happens in
 * panel.html (see panel.js).
 */
chrome.devtools.panels.create("ReconRelay", "icons/icon48.png", "src/panel.html");
