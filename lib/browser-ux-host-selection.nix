{ lib, rolesR3Selected ? false }:
let
  targetHost = "example-primary";
  replacementsOf = projection: projection.browser-ux.configuration.replacements or { };
  selected = host: projection: name:
    let primary = projection.browser-ux.configuration.primaryCutover or null;
    in host == targetHost && ((replacementsOf projection).${name}.enable or false)
      && (primary == null || !primary.enable || (builtins.elem name primary.activeBrowsers
        && !(builtins.elem name primary.retiredBrowsers)));
in
{
  moduleForHost = host: projection:
    if host == targetHost then
      projection
    else if !(projection ? browser-ux) || !(projection.browser-ux ? configuration) then projection
    else projection // {
      browser-ux = projection.browser-ux // {
        # These sections belong only to the target host. Omit them so both
        # legacy closed schemas and current disabled defaults remain valid.
        configuration = builtins.removeAttrs projection.browser-ux.configuration [ "replacements" "primaryCutover" ]
          // lib.optionalAttrs (projection.browser-ux.configuration ? extensions) {
            extensions = builtins.removeAttrs projection.browser-ux.configuration.extensions [ "apple-proton-trial" ];
          };
      };
    };

  # Inactive R3 preserves legacy installation selection. Active R3 delegates
  # Vivaldi reference and Floorp installation to Browser UX alone.
  casksForHost = host: projection:
    if rolesR3Selected || host != targetHost then [ ]
    else (lib.optional (selected host projection "vivaldi") "vivaldi")
      ++ (lib.optional (selected host projection "floorp") "floorp");

  claimAppliesToHost = host: entry:
    if entry.owner == "browser-ux"
      && builtins.elem entry.claim.id [ "bundle:com.vivaldi.Vivaldi" "bundle:app.floorp.Floorp" ]
    then host == targetHost else true;

  # Active R3 retains core continuity claims; inactive R3 preserves legacy selection.
  activeClaimsForHost = host: projection: entries:
    if rolesR3Selected then entries else
    let
      primary = projection.browser-ux.configuration.primaryCutover or null;
      retired = if host == targetHost && primary != null && primary.enable then primary.retiredBrowsers else [];
      retiredIds = (lib.optional (builtins.elem "chrome" retired) "bundle:com.google.Chrome")
        ++ (lib.optional (builtins.elem "firefox" retired) "bundle:org.mozilla.firefox");
    in builtins.filter (entry: !(entry.owner == "core" && builtins.elem entry.claim.id retiredIds)) entries;
}
