// Clicking the toolbar icon opens the side panel next to the app you're working in.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(console.error);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "tab-identity") sendResponse({tabId: sender.tab?.id});
});
