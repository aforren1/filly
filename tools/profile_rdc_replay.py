"""Replay a RenderDoc capture and write per-action GPU durations as JSON.

This script runs inside qrenderdoc's embedded Python 3.6, not in the filly venv:

    qrenderdoc.exe --python tools/profile_rdc_replay.py

tools/profile_frame.py starts it and formats the result. Settings come from environment
variables because qrenderdoc does not pass arguments to the script:

    FILLY_RDC_CAPTURE  capture file (.rdc)
    FILLY_RDC_JSON     output file
    FILLY_RDC_REPEAT   number of timed replays (default 5); the result keeps the median
    FILLY_RDC_REMOTE   optional "host:port" of a `renderdoccmd remoteserver`. qrenderdoc exports
                       NvOptimusEnablement, so a local replay always runs on the NVIDIA GPU of an
                       Optimus laptop. A remote server started without that export replays on the
                       GPU that Windows selects for it.

Keep this file compatible with Python 3.6.
"""

import json
import os
import re
import statistics
import sys
import time
import traceback

import renderdoc as rd

# Filament's generated GLSL states the material features and the variant as preprocessor defines.
# They identify the shader when no label is present.
DEFINE = re.compile(r"^#define\s+((?:MATERIAL_HAS|VARIANT_HAS|SHADING_MODEL|BLEND_MODE)_\w+)", re.M)
SHORT = (
    ("MATERIAL_HAS_", ""), ("VARIANT_HAS_", "v:"), ("SHADING_MODEL_", "model:"),
    ("BLEND_MODE_", "blend:"),
)
# Defines that nearly every lit shader has; they add length without information.
COMMON = {
    "MATERIAL_HAS_BASE_COLOR", "MATERIAL_HAS_NORMAL", "MATERIAL_HAS_METALLIC",
    "MATERIAL_HAS_ROUGHNESS", "MATERIAL_HAS_AMBIENT_OCCLUSION", "MATERIAL_HAS_EMISSIVE",
    "MATERIAL_HAS_REFLECTANCE",
}


def shorten(define):
    for prefix, replacement in SHORT:
        if define.startswith(prefix):
            return replacement + define[len(prefix):].lower()
    return define


def open_controller(path, remote_address):
    if remote_address:
        result, remote = rd.CreateRemoteServerConnection(remote_address)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Cannot connect to remote server {}: {}".format(remote_address, result))
        remote_path = remote.CopyCaptureToRemote(path, None)
        result, controller = remote.OpenCapture(rd.RemoteServer.NoPreference, remote_path,
                                                rd.ReplayOptions(), None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Remote replay failed: {}".format(result))
        return controller, lambda: (remote.CloseCapture(controller), remote.ShutdownConnection())
    capture = rd.OpenCaptureFile()
    result = capture.OpenFile(path, "", None)
    if result != rd.ResultCode.Succeeded:
        raise RuntimeError("Cannot open {}: {}".format(path, result))
    result, controller = capture.OpenCapture(rd.ReplayOptions(), None)
    if result != rd.ResultCode.Succeeded:
        raise RuntimeError("Replay failed: {}".format(result))
    return controller, lambda: (controller.Shutdown(), capture.Shutdown())


def describe_texture(textures, names, resource):
    texture = textures.get(resource)
    if texture is None:
        return names.get(resource, "backbuffer") if resource != rd.ResourceId.Null() else ""
    return "{}x{} {}{}".format(texture.width, texture.height, texture.format.Name(),
                               " x{}".format(texture.msSamp) if texture.msSamp > 1 else "")


def flatten(controller, structured):
    """Return actions in event order with the names and event ids of their debug groups."""
    marker_flags = rd.ActionFlags.PushMarker | rd.ActionFlags.PopMarker | rd.ActionFlags.SetMarker
    flat = []

    def walk(actions, path, events):
        for action in actions:
            name = action.GetName(structured)
            if action.flags & rd.ActionFlags.PushMarker:
                walk(action.children, path + [name], events + [action.eventId])
                continue
            if not action.flags & marker_flags:
                flat.append((action, name, list(path), list(events)))
            if len(action.children):
                walk(action.children, path + [name], events + [action.eventId])

    walk(controller.GetRootActions(), [], [])
    return flat


def shader_label(controller, action):
    """Pixel shader id, its sampler names, and Filament's feature defines at a draw."""
    controller.SetFrameEvent(action.eventId, True)
    state = controller.GetPipelineState()
    reflection = state.GetShaderReflection(rd.ShaderStage.Pixel)
    if reflection is None:
        return "", ""
    shader = str(state.GetShader(rd.ShaderStage.Pixel)).split("::")[-1]
    samplers = sorted(r.name.replace("materialParams_", "").replace("sampler0_", "")
                      for r in reflection.readOnlyResources)
    source = "".join(f.contents for f in reflection.debugInfo.files)
    defines = sorted(set(DEFINE.findall(source)) - COMMON)
    return shader, "{} [{}]".format(" ".join(shorten(d) for d in defines), ",".join(samplers))


def main():
    path = os.environ["FILLY_RDC_CAPTURE"]
    output = os.environ["FILLY_RDC_JSON"]
    repeat = max(1, int(os.environ.get("FILLY_RDC_REPEAT", "5")))
    controller, close = open_controller(path, os.environ.get("FILLY_RDC_REMOTE"))
    try:
        structured = controller.GetStructuredFile()
        properties = controller.GetAPIProperties()
        textures = {t.resourceId: t for t in controller.GetTextures()}
        names = {r.resourceId: r.name for r in controller.GetResources()}
        counter = rd.GPUCounter.EventGPUDuration
        if counter not in controller.EnumerateCounters():
            raise RuntimeError("This replay does not support EventGPUDuration")
        samples = {}
        started = time.perf_counter()
        for _ in range(repeat):
            for result in controller.FetchCounters([counter]):
                samples.setdefault(result.eventId, []).append(result.value.d)
        replay_s = time.perf_counter() - started
        actions = []
        for action, name, path_, path_events in flatten(controller, structured):
            durations = samples.get(action.eventId)
            outputs = [o for o in action.outputs if o != rd.ResourceId.Null()]
            entry = {
                "event": action.eventId,
                "name": name,
                "path": path_,
                "path_events": path_events,
                "flags": str(action.flags).replace("ActionFlags.", ""),
                "target": describe_texture(textures, names, outputs[0]) if outputs else "",
                "depth": describe_texture(textures, names, action.depthOut),
                "gpu_ms": statistics.median(durations) * 1e3 if durations else None,
                "gpu_ms_min": min(durations) * 1e3 if durations else None,
                "gpu_ms_max": max(durations) * 1e3 if durations else None,
                "indices": action.numIndices,
                "instances": action.numInstances,
            }
            if action.flags & rd.ActionFlags.Drawcall:
                entry["shader"], entry["shader_label"] = shader_label(controller, action)
            actions.append(entry)
        result = {
            "capture": path,
            "replay_api": str(properties.localRenderer),
            "replay_vendor": str(properties.vendor).replace("GPUVendor.", ""),
            "remote": os.environ.get("FILLY_RDC_REMOTE", ""),
            "repeat": repeat,
            "replay_seconds": replay_s,
            "actions": actions,
        }
    finally:
        close()
    with open(output, "w") as stream:
        json.dump(result, stream, indent=1)


if __name__ == "__main__":
    try:
        main()
        code = 0
    except Exception:
        with open(os.environ.get("FILLY_RDC_JSON", "profile_rdc_replay.json") + ".error", "w") as stream:
            stream.write(traceback.format_exc())
        code = 1
    sys.stdout.flush()
    # qrenderdoc would otherwise open its main window after the script.
    os._exit(code)
