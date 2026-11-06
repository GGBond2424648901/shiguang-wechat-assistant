#ifndef AppVersion
  #error AppVersion required
#endif
#ifndef Payload
  #error Payload required
#endif
#ifndef OutDir
  #error OutDir required
#endif
[Setup]
AppId={{86BF0513-B482-4378-972A-5CEE40CE9353}
AppName=拾光微信聊天助手
AppVerName=拾光微信聊天助手 {#AppVersion}
AppVersion={#AppVersion}
AppPublisher=Shiguang WeChat Assistant contributors
DefaultDirName={localappdata}\Programs\XiaomaoAssistant
DefaultGroupName=拾光微信聊天助手
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Xiaomao.exe
OutputDir={#OutDir}
OutputBaseFilename=Shiguang-WeChat-Assistant-{#AppVersion}-windows-x64-setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
#ifdef AppIcon
SetupIconFile={#AppIcon}
#endif

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[InstallDelete]
Type: files; Name: "{userprograms}\小猫助手\小猫助手.lnk"
Type: dirifempty; Name: "{userprograms}\小猫助手"

[Icons]
Name: "{group}\拾光微信聊天助手"; Filename: "{app}\Xiaomao.exe"
Name: "{userdesktop}\拾光微信聊天助手"; Filename: "{app}\Xiaomao.exe"; Tasks: desktopicon

