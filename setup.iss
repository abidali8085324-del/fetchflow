[Setup]
AppName=FetchFlow
AppVersion=1.0
DefaultDirName={autopf}\FetchFlow
DefaultGroupName=FetchFlow
OutputDir=output
OutputBaseFilename=FetchFlow_Setup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes

[Files]
Source: "dist\FetchFlow.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\FetchFlow"; Filename: "{app}\FetchFlow.exe"
Name: "{autodesktop}\FetchFlow"; Filename: "{app}\FetchFlow.exe"

[Run]
Filename: "{app}\FetchFlow.exe"; Description: "FetchFlow kholein"; Flags: nowait postinstall skipifsilent
