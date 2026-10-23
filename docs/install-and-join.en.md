# Install Research OS and join a team

For Research OS **0.3.0**, using macOS and personal OneDrive accounts.

These instructions install the current source from `main`. Published tags
are listed on the repository's [Releases page](https://github.com/LinyingL/research-os/releases).

Everyone completes the installation first. Members then follow Part 2;
project leads follow Part 3.

[Project overview](../README.md)

## Interface language

To switch to English, click **English** at the bottom of the sidebar,
next to **记录** (Records) and **停止服务** (Stop service). Click **中文**
to switch back. Some forms and messages are still in Chinese; their labels
appear below in bold, followed by a translation.

## Part 1: Install

You need a Mac with macOS 12 or later, a personal Microsoft account, and an
internet connection.

### 1. Install OneDrive

Install Microsoft OneDrive from the App Store and sign in with your
Microsoft account. OneDrive will appear in the Finder sidebar. Use this
account's e-mail address for the project invitations.

### 2. Install Apple's command line tools

Press Command + Space, search for Terminal, and open it. Run:

```bash
xcode-select --install
```

Follow the installation window. If Terminal says `already installed`,
continue to the next step.

### 3. Install uv

uv installs Research OS and the Python environment it needs. Run:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After `everything's installed!` appears, close and reopen Terminal. Check
that uv is available:

```bash
uv --version
```

It should display a version number.

### 4. Install Research OS

```bash
uv tool install git+https://github.com/LinyingL/research-os@main
```

A successful installation shows `Installed 2 executables: rce, researchos`.
For other messages, see Troubleshooting below.

### 5. Build and open the app

```bash
researchos app
```

The app is saved to `~/Applications/Research OS.app`. Press Command + Space
and search for Research OS to open it, or open it from your home folder's
Applications folder in the Finder.

Allow access when macOS asks to read Documents or OneDrive.

## Part 2: Join as a member

### 1. Send your public key to the PI

1. Click **Join a project from the shared folder…**.
2. Under **1. 我是谁** (1. Who I am), enter your **名字** (Name) and the
   **邮箱** (E-mail) of your OneDrive account, then click **保存** (Save).
3. Under **2. 我的公钥** (2. My public key), click **生成我的公钥** (Generate my public key).
4. Click **复制** (Copy). Send the full line beginning with `ssh-ed25519`
   and your e-mail address to the PI. If the button says
   **请手动选中复制** (Select and copy manually), select the text and press Command + C.

Click **关闭** (Close) while you wait for the invitations. You send the
public key; the private key is stored on your Mac.

### 2. Accept the two folder invitations

The PI will share two OneDrive folders with you:

| Folder | Purpose | Your permission |
| --- | --- | --- |
| `已发布` | The team's official version | Can view |
| A folder named after your e-mail address | Your proposals | Can edit |

Open each invitation and choose Add shortcut to My files in OneDrive on
the web. Some pages call it Add to My files. You can also find the folders
under Shared, then Shared with you.

Find both folders in OneDrive in the Finder. Right-click each, choose
Always Keep on This Device, and wait for the downloads to finish.

### 3. Choose the folders and join

Return to **Join a project from the shared folder…**. Under
**3. 公盘和工作副本** (3. Shared folder and working copy), click **选择…**
(Choose…) for each field:

| Field | Location |
| --- | --- |
| **公盘文件夹，或 PI 共享给你的「已发布」文件夹** (Shared folder, or the published folder the PI shared with you) | The `已发布` folder in OneDrive |
| **你自己的提议文件夹（PI 单独共享给你时才选；也可以以后在「成员」里选）** (Your own proposals folder, if shared separately; you can also set it later on the Members page) | The OneDrive folder named after your e-mail address |
| **工作副本放在哪里（一个新的或空的文件夹）** (Where to put your working copy; a new or empty folder) | A new, empty folder on your Mac, outside OneDrive, such as Documents/my-paper-copy |

Click **查看** (Check) to check the folders and permissions. The panel shows
**PI 公钥指纹** (PI's public key fingerprint), beginning with `SHA256:`.
Compare it with the PI by phone or in person. If it matches, click
**核对无误，加入** (Checked, join). If it differs, contact the PI before joining.

### 4. Sync the team version

On **Members**, under **公盘与同步** (Shared folder and sync), click
**同步** (Sync). You can start work when it says **Already the latest official version**
and your name appears under **团队** (Team). If your name is
missing, ask the PI to add you and publish, then sync again.

## Part 3: Set up a team as PI

### 1. Open the project and create the member list

1. Click **Add project…**, then **选择文件夹…** (Choose folder…), and select
   the project. Review the scan scope and click **加入并扫描** (Add and scan).
2. Click the project name at the top of the sidebar and choose
   **Start collaborating…** to open **Members**. Under **我是谁** (Who I am),
   enter your name and OneDrive e-mail address, then click **保存** (Save).
3. Under **开始协作** (Start collaborating), click **建立成员名单** (Create the member list).

While you are the only person, the sidebar has no **Members** or
**Proposals**. Once you have created the member list, both appear in it.

Back up `~/.researchos/keys` to a secure location. Losing the private key
requires a new key and a new fingerprint check for every member.

### 2. Set the shared folder and publish

1. Create a team folder in OneDrive, outside the project folder. Right-click
   it and choose Always Keep on This Device.
2. On **Members**, under **公盘** (Shared folder), click **选择…** (Choose…),
   select the folder, then click **设为公盘** (Set as shared folder).
3. Click **发布** (Publish). Research OS creates `已发布` and writes the
   first version.

### 3. Add members and share their folders

1. After receiving a member's public key, enter their e-mail and name under
   **添加成员** (Add member). Paste the key into **公钥文字** (Public key
   text), or use **选择…** (Choose…) beside **公钥文件** (Public key file)
   if they sent a file.
2. Click **添加成员** (Add member), then **发布** (Publish). Their folder
   is created inside `提议`, named after their e-mail address.
3. Open [OneDrive on the web](https://onedrive.live.com). Select each folder,
   click Share, set the permissions below, and send the invitation.

| Folder to share | Recipient | Permission |
| --- | --- | --- |
| `已发布` | Team members | Can view |
| The member's folder inside `提议` | That member | Can edit |

Share these folders individually. Leave the shared root and the whole
`提议` folder unshared.

### 4. Confirm your fingerprint

On **Members**, under **我的公钥** (My public key), find **指纹：** (Fingerprint:).
Compare this `SHA256:` value with the member by phone or in
person when they join.

## Daily use

### Members: submit changes

1. Click **同步** (Sync) on **Members**, then edit your local working copy.
2. Open **Proposals**. Under **Submit a proposal**, choose
   **New attempt**, **Change a research assessment**,
   **Variable definition**, or **Files only**. Add an explanation
   and click **Review what will be sent**.
3. Check the file list. If you change the selection, click
   **Review again with the ticked files**, then **Send proposal**.
4. Check **Proposals** for **Proposed**, **Approved**, or **Returned**, with
   the reason. Sync again when it says **Approved, awaiting sync**.

Proposals and replies arrive after OneDrive syncs. Comments, link judgments,
and changes made with **Link files** or **Add a source**
travel with sync without individual approval.
Lines between research attempts require the PI's approval.

### PI: review proposals

Open **Proposals**, inspect the changes, and choose:

| Action | Result |
| --- | --- |
| **Approve** | Applies the change to the project and publishes a version |
| **Return with reason** | Sends it back to the member with your explanation |
| **Discuss** | Leaves a comment and keeps the proposal open |

After editing the project files directly, click **发布** (Publish) on
**Members**. Members receive the update on their next sync.

## When both sides changed the same file

A proposal marked **Changed on both sides** cannot yet be approved. Ask the
member to sync, compare the two copies, and submit again. The attempt table
is compared by row; edits to other rows do not block this proposal.

On a member's sync, the local file is kept and the official version is
placed beside it, for example `16-build.official.py`. Compare them under
**放在旁边的正式版本** (Official versions set aside) on **Members**. Keep
the content you need in the original file, delete the `.official` copy,
and submit a new proposal.

## Troubleshooting

### `is not on your PATH`, or the researchos command cannot be found

Run this command, then reopen Terminal:

```bash
uv tool update-shell
```

### `Git operation failed` or `could not read Username` during installation

Check your connection, the repository address in the install command, and
your GitHub access. If installation still fails, send the last few error
lines to the PI or report them in the project's [Issues](https://github.com/LinyingL/research-os/issues).

### The installer only reports `Installed 1 executable: rce`

An older version was installed. Remove it, then install again using the
address in Install Research OS:

```bash
uv tool uninstall rce
```

### The app cannot access Documents

Open System Settings, then Privacy & Security, then Files and Folders.
Allow Research OS to access Documents and reopen the app.

### A shared folder has a cloud icon, or a proposal has not arrived

In the Finder, right-click the folder and choose Always Keep on This
Device. Wait for OneDrive to finish downloading and syncing. Members also
need to click **同步** (Sync) in Research OS.

## Updating

Run these commands in Terminal, in order:

```bash
uv tool install --force git+https://github.com/LinyingL/research-os@main
```

```bash
researchos app
```

Run `researchos app` after installing so the app has the current menus and
the session needed to save changes. Reopen the app. If macOS asks for file
access again, allow it.
