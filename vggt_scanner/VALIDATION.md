# Validation record

Passed:
- 12 Python backend/geometry/resource tests.
- JavaScript control-flow checks using DOM, API and WebSocket fixtures.
- Python syntax compilation and JavaScript syntax validation.
- Real upstream VGGT construction with empty weights.
- Real pretrained checkpoint parameter-schema matching for the pinned camera/depth architecture, with weights kept on the meta device.

The tests demonstrate that the application can keep capturing during a background job, use an immutable snapshot, queue a final pass, write colored PLY from valid predicted geometry, and preserve a successful result after a later failure or cancellation.

Not completed:
- Full pretrained VGGT inference in this workspace.
- Reconstruction quality on the user's captured object.
- Full browser visual/hardware-webcam testing.
- CUDA execution on the user's GPU.

A large-model check preceded the execution environment becoming unavailable. Its cause was not conclusively established. This version adds CPU/GPU memory preflights and uses smaller defaults; the large pretrained check was not repeated. Run check_setup.py, then smoke_test.py with overlapping photos to validate the full model on your own machine.
