// "Paper Tablet.exe" at the top of the portable folder: starts the bundled runtime on pc\app.py.
// The runtime's own exe can't sit up here, it needs the Python DLLs beside it. build.ps1
// compiles this with the C# compiler that ships with Windows (.NET Framework 4).
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("Paper Tablet")]
[assembly: AssemblyProduct("Paper Tablet")]
[assembly: AssemblyVersion(Launcher.Version)]
[assembly: AssemblyFileVersion(Launcher.Version)]

static class Launcher
{
    public const string Version = "0.0.0.0";  // replaced by build.ps1

    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string exe = Path.Combine(root, @"runtime\python\Paper Tablet.exe");
        string app = Path.Combine(root, @"pc\app.py");
        if (!File.Exists(exe) || !File.Exists(app))
        {
            MessageBox.Show("Paper Tablet's files are missing next to this program.\n\n" +
                "Unzip the whole PaperTablet-Portable zip and start Paper Tablet from the unzipped folder.",
                "Paper Tablet", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        Process.Start(new ProcessStartInfo(exe, "\"" + app + "\"") { WorkingDirectory = root, UseShellExecute = false });
    }
}
