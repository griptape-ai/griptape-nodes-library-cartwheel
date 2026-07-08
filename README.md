# Cartwheel Nodes for Griptape

This library provides Griptape Nodes for the [Cartwheel](https://www.getcartwheel.com/) motion orchestration API, enabling AI-driven 3D character animation, text- and video-based motion generation, and mascot creation directly within your Griptape workflows.

## Features

- **Character creation**: Upload a 3D model and thumbnail to Cartwheel and prepare it for animation.
- **Motion generation**: Generate motion for a character from a text prompt or a reference video, then download the resulting animation.
- **Mascot creation**: Turn a source video and reference images into a stylized mascot output video.
- **Rich export options**: Export motion in a wide range of formats (BVH, FBX for Blender/Maya/Unreal/Roblox, GLB, and more), with control over frame rate, axis orientation, IK, face expression, and other parameters.

## Installation

1. Clone this repository into your Griptape Nodes workspace directory:

```bash
# Navigate to your workspace directory
# On Mac or Linux you can use the command below to print your workspace directory
cd $(gtn config show | grep workspace_directory | cut -d'"' -f4)
# On Windows, the default workspace directory is a directory named GriptapeNodes in your home directory.
# Usually this is C:\Users\<username>\GriptapeNodes

# Clone the repository
git clone https://github.com/griptape-ai/griptape-nodes-library-cartwheel.git
```

2. Install dependencies:

```bash
cd griptape-nodes-library-cartwheel
uv sync
```

## API Key Setup

You'll need a Cartwheel API key to use these nodes.

### Get Your API Key

1. Visit [Cartwheel](https://www.getcartwheel.com/) and sign in to your account.
2. Navigate to your API settings.
3. Generate a new API key.

### Configure Your API Key

Configure your API key through the Griptape Nodes IDE:

1. Open the **Settings** menu.
2. Navigate to the **API Keys & Secrets** panel.
3. Add your `CARTWHEEL_API_KEY` in the respective field.

## Add your library to your installed Engine!

If you haven't already installed your Griptape Nodes engine, follow the installation steps [HERE](https://github.com/griptape-ai/griptape-nodes).
After you've completed those and you have your engine up and running:

1. Copy the path to your `griptape-nodes-library.json` file. Right click on the file, and `Copy Path` (Not `Copy Relative Path`).
2. Start up the engine!
3. Navigate to settings.
4. Open your settings and go to the App Events tab. Add an item in **Libraries to Register**.
5. Paste your copied `griptape-nodes-library.json` path from earlier into the new item.
6. Exit out of Settings. It will save automatically!
7. Open up the **Libraries** dropdown on the left sidebar.
8. Your newly registered library should appear! Drag and drop nodes to use them!

## Available Nodes

### Cartwheel Create Character

Uploads a character model and thumbnail to Cartwheel, then polls until processing finishes.

- **Character Model**: 3D model file to upload to Cartwheel.
- **Thumbnail Image**: Thumbnail image for the character.
- **Character Name**: Optional character name.
- **Character Description**: Optional character description.
- **Max Poll Attempts**: Maximum number of status checks before timing out.

Outputs include the Cartwheel **Character ID** (used by the Generate Motion node), the uploaded model URL, the thumbnail URL, and the raw character response.

### Cartwheel Generate Motion

Generates motion from text or a reference video, polls the batch, and downloads the resulting zip.

- **Character ID**: Cartwheel character ID to animate.
- **Prompt**: Text prompt used when generating motion without a reference video.
- **Reference Video**: Optional reference video. When set, the node uses Cartwheel's motion-from-video endpoint.
- **Export Type**: Motion export format (`bvh`, `fbx-blender`, `fbx-maya`, `fbx-unreal`, `fbx-roblox`, `glb`, `ma`, `mb`).
- **Requested Model**: Model used by Cartwheel for text-based motion generation.
- **Requested Duration**: Requested motion duration in seconds (text-based generation).
- **Face Expression**: Face expression to apply.
- **Forward / Up**: Axis orientation for the export.
- **Frame Rate / Frame Step Size**: Output frame rate and step size.
- **Hand Pose**: Hand pose to apply (not applicable for motion-from-video).
- **IK Feet / IK Hands**: Use inverse kinematics for feet/hands (applicable to Maya exports).
- **Include Mesh, Keyframe Cleaning, Move In Place, Loop, Skin Hex, Rotation Offsets**: Additional export and post-processing controls.

Outputs include the batch ID, motion IDs, the downloaded zip URL and its entries, preview video URLs, and BVH URLs.

### Cartwheel Create Mascot

Uploads mascot source assets, submits a mascot job, polls for completion, and downloads the generated output video.

- **Input Video**: Source video to process into a mascot output.
- **Character Reference Images**: Reference images for the target character replacement (the first image is the first target).
- **Environment Image**: Optional environment or scene image.
- **Prompt**: Text prompt describing the character replacement.
- **Mascot Name / Batch Name**: Optional names for the mascot job and batch.
- **Input Video / Reference Image / Environment Image Resolution**: Optional resolutions for each asset, for example `1920x1080`.
- **Max Poll Attempts**: Maximum number of status checks before timing out.

Outputs include the mascot ID, batch ID, the output video URL, the downloaded output video, and uploaded media IDs.

## Example Workflow

### Text-to-Motion Pipeline

1. Add a **Cartwheel Create Character** node and connect a 3D model and thumbnail. Run it to obtain a **Character ID**.
2. Add a **Cartwheel Generate Motion** node and connect the **Character ID** output from the previous node.
3. Set a **Prompt** describing the motion you want (or connect a **Reference Video** for motion-from-video).
4. Choose an **Export Type** and adjust orientation, frame rate, and other options as needed.
5. Run the workflow. The generated motion is downloaded as a zip, with individual asset URLs available on the node's outputs.

## Troubleshooting

**"Cartwheel API key not found"**
- Ensure your `CARTWHEEL_API_KEY` secret is configured in the Griptape Nodes IDE Settings → API Keys & Secrets.

**Job times out**
- Long-running jobs may exceed the default polling window. Increase **Max Poll Attempts** on the node.

**Motion generation errors**
- Confirm the **Character ID** is valid and that the character finished processing in the Create Character node before generating motion.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
