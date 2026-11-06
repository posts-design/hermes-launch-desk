// Launch Desk has no web dashboard tab (the manifest hides it); its page lives in Hermes Desktop.
// This registers a small placeholder so a direct visit to /launch-desk says where to look.
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__;
  var React = SDK.React;

  function LaunchDeskPlaceholder() {
    return React.createElement("p", { className: "p-4 text-sm text-muted-foreground" },
      "Launch Desk shows your packs in Hermes Desktop. Run /launch in a chat to make one.");
  }

  window.__HERMES_PLUGINS__.register("launch-desk", LaunchDeskPlaceholder);
})();
