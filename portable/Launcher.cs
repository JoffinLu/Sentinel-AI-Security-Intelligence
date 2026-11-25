using System;
using System.Diagnostics;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

internal static class Launcher {
    private const uint JobObjectLimitKillOnJobClose = 0x00002000;
    private const int JobObjectExtendedLimitInformation = 9;

    // Pointer-sized fields preserve the native layout on both .NET x86/x64.
    [StructLayout(LayoutKind.Sequential)]
    private struct JobBasicLimits {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct JobIoCounters {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct JobExtendedLimits {
        public JobBasicLimits BasicLimitInformation;
        public JobIoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass,
        ref JobExtendedLimits info, uint length);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr handle);

    private static IntPtr CreateOwnedJob() {
        // Unnamed and non-inheritable: only this launcher owns the job handle.
        IntPtr job=CreateJobObject(IntPtr.Zero,null);
        if(job==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(),"无法创建本包进程容器。");
        var limits=new JobExtendedLimits();
        limits.BasicLimitInformation.LimitFlags=JobObjectLimitKillOnJobClose;
        if(!SetInformationJobObject(job,JobObjectExtendedLimitInformation,
                ref limits,(uint)Marshal.SizeOf(typeof(JobExtendedLimits)))) {
            int error=Marshal.GetLastWin32Error(); CloseHandle(job);
            throw new Win32Exception(error,"无法设置本包进程退出保护。");
        }
        return job;
    }
    private static string Quote(string value) {
        var b=new StringBuilder("\""); int slashes=0;
        foreach(char c in value) {
            if(c=='\\') { slashes++; continue; }
            if(c=='\"') { b.Append('\\',slashes*2+1); b.Append(c); slashes=0; continue; }
            b.Append('\\',slashes); slashes=0; b.Append(c);
        }
        b.Append('\\',slashes*2); b.Append('"'); return b.ToString();
    }
    [STAThread]
    private static void Main(string[] args) {
        string root=AppDomain.CurrentDomain.BaseDirectory;
        string exe=Path.Combine(root,"runtime","python","pythonw.exe");
        string script=Path.Combine(root,"portable_bootstrap.py");
        if(!File.Exists(exe)||!File.Exists(script)) {
            MessageBox.Show("请先将整个 ZIP 解压到文件夹，再双击 Sentinel.exe。请保留 runtime、app、data 等目录。","Sentinel"); return;
        }
        var command=new StringBuilder("-X utf8 -u "+Quote(script));
        if(Path.GetFileNameWithoutExtension(Application.ExecutablePath).IndexOf("停止",StringComparison.Ordinal)>=0) command.Append(" --stop");
        foreach(string a in args) command.Append(" "+Quote(a));
        var start=new ProcessStartInfo(exe,command.ToString());
        start.WorkingDirectory=root; start.UseShellExecute=false; start.CreateNoWindow=true;
        start.EnvironmentVariables.Remove("PYTHONHOME"); start.EnvironmentVariables.Remove("PYTHONPATH");
        start.EnvironmentVariables["PYTHONUTF8"]="1";
        IntPtr ownedJob=IntPtr.Zero;
        Process child=null;
        bool assigned=false;
        try {
            ownedJob=CreateOwnedJob();
            child=Process.Start(start);
            if(child==null) throw new InvalidOperationException("未能创建 Sentinel 运行进程。");
            // Assign before Python imports the app and starts any databases.
            if(!AssignProcessToJobObject(ownedJob,child.Handle))
                throw new Win32Exception(Marshal.GetLastWin32Error(),"无法绑定本包进程退出保护。");
            assigned=true;
            // Keep the handle alive while Python performs its normal cleanup.
            // If this wrapper is terminated, Windows closes the handle and ends
            // only this job's Python, PostgreSQL and Neo4j descendants.
            child.WaitForExit();
            Environment.ExitCode=child.ExitCode;
        }
        catch(Exception e) {
            Environment.ExitCode=1;
            // A binding failure must not leave a newly started unowned app.
            if(!assigned && child!=null) {
                try { if(!child.HasExited) { child.Kill(); child.WaitForExit(5000); } }
                catch { }
            }
            MessageBox.Show("启动失败："+e.Message,"Sentinel");
        }
        finally {
            if(ownedJob!=IntPtr.Zero) CloseHandle(ownedJob);
            if(child!=null) child.Dispose();
        }
    }
}
