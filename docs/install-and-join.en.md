# Install Research OS and join a team

This guide is for researchers who do not program. Following it takes about half an hour, most of it waiting for downloads.

It has two parts:

- **Part 1: Member.** Someone (the PI) has asked you to work on a project with them. You install Research OS on your own Mac and join the project.
- **Part 2: PI.** You lead the project and want to share it with members.

After the two parts come how to use it every day, what to do when a file was changed on both sides, and what Research OS never does.

## Reading this guide on an English screen

**Switching to English.** On a Mac whose first language is not Chinese, Research OS starts in English and you do not need to do anything. On a Mac set to Chinese it starts in Chinese. To switch, look at the bottom of the sidebar on the left: beside **记录** (Records) and **停止服务** (Stop service) there is a small text button labelled **English**. Click it. The page reloads in English, and the Mac menus at the top of the screen follow. To switch back, click the same button, which then reads **中文** (Chinese).

The choice is yours and belongs to this Mac only. It changes the words on your screen and nothing else: the files and folders Research OS writes are the same in either language, so you and a colleague whose screen is in Chinese work in the same shared folder.

**What is in English so far.** In this version the sidebar (with the menu that opens when you click the project's name), the page titles, the welcome page, the **Desk**, the **New attempt** button at the top of **Research routes** (for a member it reads **Propose a new attempt**) and the Mac menus are in English. The **Members** and **Proposals** pages, what the other sections show, the side panels and forms (such as the panel that opens when you join a project, or the form behind **New attempt**) and error messages are still in Chinese. A few words on those pages are already in English, because the **Desk** uses them too: dates, verdict words such as **Current** and **Abandoned**, the one-line description of a proposal, and what a sync will write and what it did. For every button or field that is still in Chinese, this guide gives the label exactly as it appears on screen, in bold, followed by its meaning in brackets, for example **生成我的公钥** (Generate my public key). Look for the same characters on screen. If Research OS shows a message you cannot read, take a screenshot and send it to the PI.

**Two folder names are always Chinese**, whatever the language of the screen, because they are names on disk that the whole team shares: `已发布` (published), where the PI's official versions go, and `提议` (proposals), where each member's proposals go.

## Before you start

You need:

1. A Mac.
2. A personal Microsoft account (OneDrive signs in with it). The PI shares folders with this account's e-mail address.
3. An internet connection.

A few steps ask you to type commands in Terminal. To open Terminal: hold down the Command key and press the Space bar, type Terminal, and press Return. Every command is in a grey box: copy the whole line, paste it into Terminal, press Return, and wait until it has finished (the line where you can type appears again) before the next step. You do not need to understand all the English Terminal prints; this guide tells you which words to look for.

## Part 1: Member

### 1. Install OneDrive and sign in

1. Open the App Store, search for OneDrive, and install Microsoft OneDrive.
2. Open OneDrive and sign in with your personal Microsoft account.
3. When it asks where to put your OneDrive folder, keep the default location.
4. When it is done, OneDrive appears in the Finder's sidebar, under Locations.

### 2. Install Apple's command line tools

Research OS uses Apple's command line tools to build itself into a Mac app, and the installer uses them to download Research OS from GitHub. In Terminal, type:

```bash
xcode-select --install
```

1. A window asks whether you want to install the command line developer tools now. Click Install, then click Agree to accept Apple's licence agreement.
2. Wait until they have downloaded and installed (from a few minutes to half an hour), then click Done.
3. If Terminal shows the words `already installed`, this Mac already has them: go straight to the next step.

### 3. Install uv

uv is an installer that brings its own copy of Python, the one Research OS needs. The Python that comes with macOS is too old for Research OS, so you do not need to install Python yourself, and should not. In Terminal, type:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

1. When the last lines include `everything's installed!`, it is installed.
2. Quit Terminal (press Command and Q while in Terminal) and open a new Terminal. Only then can Terminal find the uv you have just installed.
3. Type the line below to check. It shows a version number, for example `uv 0.10.4`:

```bash
uv --version
```

### 4. Install Research OS

In Terminal, type:

```bash
uv tool install git+https://github.com/LinyingL/research-os
```

1. The first time, uv downloads Python and then Research OS. This takes a minute or two.
2. When the last lines include `Installed 2 executables: rce, researchos`, it is installed.
3. If the last lines include `is not on your PATH`, type the line below, then quit Terminal and open it again:

```bash
uv tool update-shell
```

4. If you do not see `Installed 2 executables: rce, researchos`, but see `Git operation failed`, `could not read Username` or `Installed 1 executable: rce` instead, the new version that works is not on GitHub yet. Do not try another address, and do not go on. Take a photo of the last lines in Terminal, or copy them, and send them to the PI. If you saw `Installed 1 executable: rce`, first type the line below to remove the old version it installed, and do this step again once the PI tells you to:

```bash
uv tool uninstall rce
```

### 5. Put Research OS in Applications

In Terminal, type:

```bash
researchos app
```

It makes Research OS.app in the Applications folder inside your home folder. It prints `Research OS.app written to` followed by that place, and its last line begins with `双击 Research OS.app 即可打开研究地图` (Double-click Research OS.app to open the research map). This step compiles the app and takes from a few seconds to a minute. After it, you no longer need Terminal.

### 6. Open Research OS for the first time

1. Hold down the Command key and press the Space bar, type Research OS, and press Return. Or, in the Finder, choose Home from the Go menu, open the Applications folder and double-click Research OS.
2. To open it more easily later: in the Dock, right-click the Research OS icon, choose Options, then Keep in Dock.
3. The first time Research OS reads files in your Documents folder, macOS asks whether “Research OS” may access files in your Documents folder. Click OK or Allow (the wording depends on the version of macOS). If you clicked Don't Allow: open System Settings, click Privacy & Security, then Files and Folders, find Research OS, and turn on the Documents Folder switch.
4. If macOS asks whether Research OS may access files in OneDrive, click Allow as well.

The first time, there is no project yet, and the page shows two buttons: **Add project…** and **Join a project from the shared folder…**.

### 7. Say who you are and send your public key to the PI

1. Click **Join a project from the shared folder…**. A panel opens on the right; it is in Chinese.
2. Under **1. 我是谁** (1. Who I am), fill in your name in **名字** (Name) and the e-mail address of your OneDrive account in **邮箱** (E-mail), then click **保存** (Save). The PI will share the folders with this address; it is also your ID in the team.
3. Under **2. 我的公钥** (2. My public key), click **生成我的公钥** (Generate my public key). A line of text beginning with `ssh-ed25519` appears.
4. Click **复制** (Copy) below it, paste it into an e-mail or a chat message, and send it to the PI together with your e-mail address. If the button changes to **请手动选中复制** (Please select and copy by hand): click once on the line of text (the whole line is selected) and press Command and C to copy it.

Your public key is safe to send: it only lets the PI confirm that a proposal really comes from you. The private key that goes with it stays on your Mac and never leaves it.

After sending it, you can click **关闭** (Close) at the top right of the panel and wait for the PI's invitations. Once a project is open, all of this is on the **Members** page in the sidebar.

### 8. Accept the PI's two sharing invitations

After adding you to the team, the PI shares two folders with you in OneDrive, and you receive two e-mails from OneDrive:

- `已发布` (published): the project's official version. You **can only view** it.
- A folder named after your e-mail address, for example `wang@example.com`: your own proposals folder. You **can edit** it.

Each of the two folders has to be added to your own OneDrive before it syncs to your Mac. For each e-mail:

1. In the e-mail, click Open. Your browser opens OneDrive on the web; if it asks you to sign in, use your personal Microsoft account.
2. At the top of the folder's page, click Add shortcut to My files. Some versions call it Add to My files; it is the same button.
3. If you cannot find the e-mail: in OneDrive on the web, click Shared on the left, then Shared with you (some versions say With you), right-click the folder and choose Add shortcut to My files (or Add to My files).

### 9. Wait for the two folders to sync to your Mac

1. Open the Finder and click OneDrive in the sidebar. Within a few minutes, `已发布` and the folder named after your e-mail address appear.
2. Right-click each of the two folders and choose Always Keep on This Device. Research OS does not read files that are only in the cloud and not yet downloaded to your Mac.
3. Wait until the cloud icon next to each folder has turned into a check mark, and the OneDrive icon in the menu bar at the top of the screen no longer shows that it is syncing.

### 10. Choose the two folders, compare the PI's fingerprint, and join

1. Go back to Research OS and click **Join a project from the shared folder…**. If another project is already open, click the project's name under **Project** at the top of the sidebar, and choose **Join a project from the shared folder…** in the menu that opens.
2. Under **3. 公盘和工作副本** (3. Shared folder and working copy), click **选择…** (Choose…) for each of the three fields:
   - **公盘文件夹，或 PI 共享给你的「已发布」文件夹** (Shared folder, or the `已发布` folder the PI shared with you): choose `已发布` in OneDrive.
   - **你自己的提议文件夹（PI 单独共享给你时才选；也可以以后在「成员」里选）** (Your own proposals folder; choose it only when the PI shares it with you on its own; you can also choose it later on the **Members** page): choose the folder in OneDrive named after your e-mail address.
   - **工作副本放在哪里（一个新的或空的文件夹）** (Where to put the working copy; a new or empty folder): where your own copy of the project will live. In the window that opens, go to Documents, click New Folder, give it a name, for example “FX paper my copy”, and choose it. It must be a new, empty folder; do not put it inside OneDrive.
3. Click **查看** (Check). Research OS first checks that `已发布` holds a version of this project signed by the PI, and that you can write into your own proposals folder. Up to this point it has written nothing.
4. The panel shows **PI 公钥指纹** (PI's public key fingerprint), a string of letters and digits beginning with `SHA256:`. Phone the PI, or use another way than the one you used to send your public key (in person, for example), and ask the PI to read out or send you their fingerprint. Compare the two character by character.
5. Only if they match exactly, click **核对无误，加入** (Checked, join). If they do not match, do not join: tell the PI first.

After you join, Research OS opens your working copy, scans it, and shows the **Members** page. From now on it accepts only versions signed with this key.

### 11. Sync

1. On the **Members** page, under **公盘与同步** (Shared folder and sync), you can see which version your working copy is based on, and the two folders you chose.
2. Click **同步** (Sync). If the PI has already published since adding you to the team, it says **Already the latest official version**; otherwise, wait until the PI publishes and sync once more.
3. When your name appears under **团队** (Team), you are a member.

## Part 2: PI

### 1. Install

Like a member, do steps 1 to 6 of Part 1: install OneDrive and sign in, install the command line tools, uv and Research OS, run `researchos app`, and open the app for the first time. Install it yourself before you ask members to: if step 4 does not show `Installed 2 executables: rce, researchos`, do not ask members to install yet.

### 2. Open your project

If Research OS does not have this project yet: click **Add project…**, click **选择文件夹…** (Choose folder…) and choose your project folder. Research OS first lists which files it will read and what it will write; once you have read the list, click **加入并扫描** (Add and scan).

### 3. Set yourself up on the Members page

1. Click **Members** in the sidebar.
2. Under **我是谁** (Who I am), fill in your name in **名字** (Name) and the e-mail address of your OneDrive account in **邮箱** (E-mail), then click **保存** (Save).
3. Under **开始协作** (Start collaborating), click **建立成员名单** (Create the member list). You are now this project's PI. A pair of signing keys is made on this Mac; the private key never leaves it.

Back up the `keys` folder that holds your signing keys, for example by copying it to a USB stick (in the Finder, choose Go to Folder… from the Go menu, type `~/.researchos/keys` and press Return). A lost private key cannot be recovered: you would have to make a new one, and every member would have to compare fingerprints again.

### 4. Choose the shared folder and publish the first version

1. In the Finder, open OneDrive and make a new folder for this project's team, for example “FX paper team”. It must not be inside the project folder.
2. Right-click this folder and choose Always Keep on This Device.
3. Back in Research OS, on the **Members** page, under **公盘** (Shared folder), click **选择…** (Choose…), choose this folder, and click **设为公盘** (Set as shared folder).
4. Click **发布** (Publish). Research OS makes `已发布` inside this folder and writes the first version into it.

### 5. Add members

Once a member has sent you their public key and e-mail address (Part 1, step 7):

1. On the **Members** page, under **添加成员** (Add member), fill in the member's e-mail address in **邮箱** (E-mail) and their name in **名字** (Name), and paste the line of public key text the member sent into **公钥文字** (Public key text). If they sent a file instead, click **选择…** (Choose…) beside **公钥文件** (Public key file) and choose the file.
2. Click **加入团队** (Add to team). Research OS makes this member's proposals folder in the shared folder: `提议` and, inside it, a folder named after the member's e-mail address.
3. Click **发布** (Publish) once more. When the member joins or syncs, they see themselves in the team.

### 6. Share two folders in OneDrive on the web

These two shares are what make “only the PI can approve” a rule rather than just an agreement. Do this once for every member:

1. Open OneDrive on the web (onedrive.live.com) in your browser and sign in with your personal Microsoft account.
2. In My files, open your shared folder (for example “FX paper team”).
3. Share `已发布` so that it can only be viewed:
   1. Move the mouse over `已发布` and click the circle at its top left corner to select it.
   2. Click Share at the top of the page.
   3. In the window that opens, click the permission setting (the pencil icon, or where it says Can edit) and change it to Can view.
   4. Type the member's e-mail address and click Send.
4. Share the member's own proposals folder so that it can be edited:
   1. Open the `提议` folder and select the folder named after this member's e-mail address.
   2. Click Share and choose Can edit as the permission.
   3. Type **this** member's e-mail address and click Send.

Do not share the whole shared folder or the whole `提议` folder, and do not share one member's proposals folder with another member.

### 7. Tell members your fingerprint

1. On the **Members** page, under **我的公钥** (My public key), after **指纹：** (Fingerprint:) there is a string of letters and digits beginning with `SHA256:`.
2. When a member joins, they will phone you or ask you in person. Read it out to them, or send it by another way than the one they used to send you their public key.

## Every day

### Member

1. Open Research OS and, on the **Members** page, click **同步** (Sync), so that you start from the latest official version.
2. Work in your working copy as usual: scripts, data, the attempt table, variable cards.
3. To hand your changes to the PI: click **Proposals** in the sidebar, and under **提交提议** (Submit a proposal) choose one kind: **新的尝试** (New attempt), **改判** (Change verdict), **变量定义** (Variable definition) or **只有文件** (Files only). Say in one sentence what it is, and click **查看将要发送的内容** (Review what will be sent). Look carefully at which files will be sent; you can tick a file to add it or untick it to leave it out. If you changed the ticks, first click **按勾选的文件重新查看** (Review again with the ticked files), then click **发送提议** (Send proposal). If a file was part of a returned proposal, a note beside it reminds you to read the reason it was returned first. You can also choose **Submit Proposal…** from the File menu.
4. The PI sees a proposal only once OneDrive has synced it to the PI's Mac, and the PI's answer has to sync back to you in the same way; this usually takes from a few seconds to a few minutes. **Proposals** shows the state of every proposal: **提议中** (Proposed), **已批准** (Approved) or **已退回** (Returned), which carries the reason the PI wrote. After the PI approves, your change is marked **已批准，待同步** (Approved, awaiting sync); sync once, and it is part of the official version.
5. Your notes, your judgments on links, and what you add with **关联文件** (Link files) and **补上来源** (Add source) go to the PI automatically at your next sync, without approval.

### PI

1. **Proposals** holds the proposals waiting for you. Open one to see how the official version would change if you approved it.
2. There are three answers. **批准并加入** (Approve): the change goes into the official version, and a new version is published automatically. **退回并说明** (Return with reason): a reason is required, and the member sees it. **讨论** (Discuss): write a sentence; the proposal stays open.
3. When you have changed the official version yourself, click **发布** (Publish) on the **Members** page (or choose **Publish** from the File menu). Members see it after they sync.

## When both sides changed the same file

Research OS never merges two people's changes for you. It shows you both, and a person decides.

- **The PI sees a proposal marked** **两边都改过** (Changed on both sides): since the member last synced, you have changed this file too, and this proposal cannot be approved now. Return it, and ask the member to sync first and then propose again. The attempt table is compared row by row: it counts as changed on both sides only if this proposal's own row (or the number a new row would use) has changed in the official version; your changes to other rows do not affect it.
- **A member's sync meets a file changed on both sides**: your copy is not touched, and the official copy is put beside it, named “original name.official.extension”, for example `16-build.official.py`. On the **Members** page, **放在旁边的官方版本** (Official versions set aside) shows how the two differ. After comparing them, keep the right content in the original file, delete the `.official` copy (once you delete it, it also disappears from **放在旁边的官方版本** (Official versions set aside)), and propose again.

## What Research OS never does

1. It never merges two people's changes to the same file.
2. It never changes anything already written in the shared folder. Every version, proposal and answer is written once under a new name and never changed afterwards. Conflict copies made by OneDrive, and anything not completely written, are reported, never read.
3. It never connects to the PI's computer, and never goes online by itself. The only link between the members of a team is the two folders OneDrive syncs.
4. It never accepts a version whose signature it cannot check. A version not signed by the PI is refused, and the last version that passed the check is kept.
5. It never sends anything before you have seen it: before a proposal is sent, it lists every file the proposal will carry.

## Updating Research OS later

In Terminal, type the two lines below, one after the other. The second one rebuilds Research OS.app when needed:

```bash
uv tool upgrade researchos
```

```bash
researchos app
```

If the update changed the app itself, macOS asks once more whether Research OS may access your Documents folder: click OK or Allow.
