export type PatchAction = "edit" | "add_textbox" | "delete_textbox" | "add_slide";

export interface SlidePatch {
  id: string;
  slide_index: number;
  shape_index: number;
  action: PatchAction;
  before?: string;
  after: string;
  description?: string;
  position?: string;
  payload?: Record<string, any> | null;
}

export interface ScanDeckResponse {
  patches: SlidePatch[];
}

export interface ApplyPatchesResponse {
  version_id: string;
}


export type GlobalState = "idle" | "analyzing" | "reviewing" | "previewing" | "done";
export type PatchState = "pending" | "previewing" | "accepted" | "declined";
export interface ReviewPatch extends SlidePatch {
  uiState: PatchState;
}
