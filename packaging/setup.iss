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
AppName=小猫助手
AppVerName=小猫助手 {#AppVersion}
AppVersion={#AppVersion}
AppPublisher=Xiaomao WeChat Assistant contributors
DefaultDirName={localappdata}\Programs\XiaomaoAssistant
DefaultGroupName=小猫助手
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Xiaomao.exe
OutputDir={#OutDir}
OutputBaseFilename=Xiaomao-WeChat-Assistant-{#AppVersion}-windows-x64-setup
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

[Icons]
Name: "{group}\小猫助手"; Filename: "{app}\Xiaomao.exe"

