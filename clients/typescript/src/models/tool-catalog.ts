/** One entry of `GET /api/tools/catalog`; mirrors `openhands.sdk.tool.registry.ToolCatalogEntry`. */
export interface ToolCatalogEntry {
  name: string;
  user_selectable: boolean;
  usable: boolean;
  description: string;
  /** Whether a profile with unset `tools` gets this tool where it is usable. */
  in_default_set: boolean;
}

export interface ToolCatalogResponse {
  tools: ToolCatalogEntry[];
}
