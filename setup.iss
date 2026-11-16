; Script de Inno Setup para Descargador YT Studio
#define MyAppName "Descargador YT Studio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Descargador YT Open Source"
#define MyAppExeName "Descargador YT.exe"

[Setup]
AppId={{8F4C9D2A-51E7-4B98-A31C-7E8F90D12B34}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=release
OutputBaseFilename=DescargadorYT_Setup
SetupIconFile=img\icono.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\Descargador YT\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\Descargador YT\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "img\icono.ico"; DestDir: "{app}\img"; Flags: ignoreversion
Source: "img\icono.jpeg"; DestDir: "{app}\img"; Flags: ignoreversion

[Dirs]
Name: "{app}\Descargas"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\img\icono.ico"
Name: "{autodesktop}\Descargador YT"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\img\icono.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
