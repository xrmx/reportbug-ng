# encoding: utf8
# rnghelpers.py - Various helpers for Reportbug-NG.
# Copyright (C) 2007-2008  Bastian Venthur
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.


import commands
import re
import os
import webbrowser
import urllib
import thread
import logging
import ConfigParser
import tempfile


logger = logging.getLogger("ReportbugNG")


RFC_MAILTO = '"mailto:%(to)s?subject=%(subject)s&body=%(body)s"'
MUA_SYNTAX = {
    "default" : 'xdg-email --utf8 --subject "%(subject)s" --body "%(body)s" "%(to)s"',
    "icedove" : 'icedove -compose ' + RFC_MAILTO,
    "iceape" : 'iceape -compose ' + RFC_MAILTO,
    "evolution" : 'evolution ' + RFC_MAILTO,
    "kmail" : 'kmail --composer --subject "%(subject)s" --body "%(body)s" "%(to)s"',
#    "opera" : 'opera -newpage ' + RFC_MAILTO,
    "sylpheed" : 'sylpheed --compose ' + RFC_MAILTO,
    "sylpheed-claws" : 'sylpheed-claws --compose ' + RFC_MAILTO,
    "sylpheed-claws-gtk2" : 'sylpheed-claws-gtk2 --compose ' + RFC_MAILTO,
    "mutt" : 'mutt ' + RFC_MAILTO,
    "mutt-ng" : 'muttng ' + RFC_MAILTO,
    "pine" : 'pine -url ' + RFC_MAILTO,
#    "googlemail" : 'https://gmail.google.com/gmail?view=cm&cmid=0&fs=1&tearoff=1&to=%(to)s&su=%(subject)s&body=%(body)s'
              }
# Don't urllib.quote() their strings
MUA_NO_URLQUOTE = ["default", "kmail"]            
# Who needs a terminal?
MUA_NEEDS_TERMINAL = ["mutt", "mutt-ng", "pine"]
# Who needs a browser?
WEBMAIL = ["googlemail"]

# HACK: don't know the maximum lenght of a command yet
MAX_BODY_LEN = 10000


# Those strings must not be translated!
WNPP_ACTIONS = ("RFP", "ITP", "RFH", "RFA", "O")
SEVERITY = ("Critical", "Grave", "Serious", "Important", "Normal", "Minor", "Wishlist")

SEVERITY_EXPLANATION = _("\
<b>%(cri)s</b> makes unrelated software on the system (or the whole system) break, or causes serious data loss, or introduces a security hole on systems where you install the package. \
<br> \
<b>%(gra)s</b> makes the package in question unusable or mostly so, or causes data loss, or introduces a security hole allowing access to the accounts of users who use the package. \
<br> \
<b>%(ser)s</b> is a severe violation of Debian policy (roughly, it violates a \"must\" or \"required\" directive), or, in the package maintainer's opinion, makes the package unsuitable for release. \
<br> \
<b>%(imp)s</b> a bug which has a major effect on the usability of a package, without rendering it completely unusable to everyone. \
<br> \
<b>%(nor)s</b> the default value, applicable to most bugs. \
<br> \
<b>%(min)s</b> a problem which doesn't affect the package's usefulness, and is presumably trivial to fix. \
<br> \
<b>%(wis)s</b> for any feature request, and also for any bugs that are very difficult to fix due to major design considerations.") % {'cri':"Critical", 'gra':"Grave", 'ser':"Serious", 'imp':"Important", 'nor':"Normal", 'min':"Minor", 'wis':"Wishlist"}

REPORTBUG_NG_INSTRUCTIONS = _("""\
<h2>Using Reportbug-NG</h2>
<h3>Step 1: Finding Bugs</h3>
<p>To find a bug just enter a query and press Enter. Loading the list might take a few seconds.</p>

<p>The following queries are supported:
<dl>
<dt><code>package</code></dt><dd>Returns all the bugs belonging to the PACKAGE</dd>
<dt><code>bugnumber</code></dt><dd>Returns the bug with BUGNUMBER</dd>
<dt><code>maintainer@foo.bar</code></dt><dd>Returns all the bugs assigned to MAINTAINER</dd>
<dt><code>src:sourcepackage</code></dt><dd>Returns all the bugs belonging to the SOURCEPACKAGE</dd>
<dt><code>from:submitter@foo.bar</code></dt><dd>Returns all the bugs filed by SUBMITTER</dd>
<dt><code>severity:foo</code></dt><dd>Returns all the bugs of SEVERITY. Warning: this list is probably very long. Recognized are the values: critical, grave, serious, important, normal, minor and wishlist</dd>
<dt><code>tag:bar</code></dt><dd>Returns all the bugs marked with TAG</dd>
</dl>
</p>

<p>To see the full bugreport click on the bug in the list. Links in the bugreport will open in an external browser when clicked.</p>

<h3>Step 2: Filtering Bugs</h3>
<p>To filter the list of existing bugs enter a few letters (without pressing Enter). The filter is case insensitive and
affects the packagename, bugnumber, summary, status and severity of a bug.</p>

<h3>Step 3: Reporting Bugs</h3>
<p>You can either provide additional information for an existing bug by clicking on the bug in the list and pressing the "Additional Info" button or you can create a new bugreport for the current package by clicking the "New Bugreport" button.</p>
""")

def getAvailableMUAs():
    """
    Returns a tuple of strings with available MUAs on this system. The Webmails
    are always available, since there is no way to check.
    """
    list = []
    for mua in MUA_SYNTAX:
        if mua in WEBMAIL:
            list.append(mua)
            continue
        command = MUA_SYNTAX[mua].split()[0]
        for p in os.defpath.split(os.pathsep):
            if os.path.exists(os.path.join(p, command)):
                list.append(mua)
                continue
    return list


SUPPORTED_MUA = getAvailableMUAs()
SUPPORTED_MUA.sort()


def prepareMail(mua, to, subject, body):
    """    print output

    Tries to call MUA with given parameters.
    """
    
    mua = mua.lower()
    
    if mua not in MUA_NO_URLQUOTE:
        subject = urllib.quote(subject.encode("ascii", "replace"))
        body = urllib.quote(body.encode("ascii", "replace"))
    else:
        to = to.encode("ascii", "replace")
        subject = subject.encode("ascii", "replace")
        body = body.encode("ascii", "replace")

    # If quotes are used for this MUA, escape the quotes in the arguments:
    if '"' in MUA_SYNTAX[mua] and MUA_SYNTAX[mua].count('"')%2 == 0:
        to = to.replace('"', '\\"')
        subject = subject.replace('"', '\\"')
        body = body.replace('"', '\\"')
    
    command = MUA_SYNTAX[mua] % {"to":to, "subject":subject, "body":body}
    
    if mua in MUA_NEEDS_TERMINAL:
        command = "x-terminal-emulator -e "+command

    if mua in WEBMAIL:
        callBrowser(command)
    else:
        status, output = callMailClient(command)
        if status == 0:
            return
        # Great, calling the MUA failed, probably due too long output of the
        # /usr/share/bug/$package/script...
        logger.warning("Grr! Calling the MUA failed. Length of the command is: %s" % str(len(command)))
        body = body[:MAX_BODY_LEN] + "\n\n[ MAILBODY EXCEEDED REASONABLE LENGTH, OUTPUT TRUNCATED ]"
        prepareMail(mua, to, subject, body)
            
   
    
def prepareBody(package, version=None, severity=None, tags=[], cc=[], script=True):
    """Prepares the empty bugreport including body and system information."""
    
    s = prepare_minimal_body(package, version, severity, tags, cc)

    s += getSystemInfo() + "\n"
    s += getDebianReleaseInfo() + "\n"
    s += getPackageInfo(package) + "\n"
    
    if not script:
        return s
    
    s2 = getPackageScriptOutput(package) + "\n"
    if len(s+s2) > MAX_BODY_LEN:
        logger.warning("Mailbody to long for os.pipe")
        fd, fname = tempfile.mkstemp(".txt", "reportbug-ng-%s-" % package)
        f = os.fdopen(fd, "w")
        f.write(s2)
        f.close()
        s2 = """
-8<---8<---8<---8<---8<---8<---8<---8<---8<--
Please attach the file: 
  %s 
to the mail. I'd do it myself if the output wasn't too long to handle.

  Thank you!
->8--->8--->8--->8--->8--->8--->8--->8--->8--""" % fname
    s += s2

    return s


def prepare_minimal_body(package, version=None, severity=None, tags=[], cc=[]):
    """Prepares the body of the empty bugreport."""
    
    s = ""
    s += "Package: %s\n" % package
    if version:
        s += "Version: %s\n" % version 
    if severity:
        s += "Severity: %s\n" % severity
    if tags:
        s += "Tags:"
        for tag in tags:
            s += " %s" % tag
        s += "\n"
    for i in cc:
            s += "X-Debbugs-CC: %s\n" % i
    s += "\n"
    s += "--- Please enter the report below this line. ---\n\n\n"

    return s


def prepare_wnpp_body(action, package, version=""):
    """Prepares a WNPP bugreport."""
    
    s = ""
    s += "Package: wnpp\n"
    if action in ("ITP", "RFP"):
        s += "Severity: wishlist\n"
    else:
        s += "Severity: normal\n"
    s += "X-Debbugs-CC: debian-devel@lists.debian.org\n"

    if action in ("ITP", "RFP"):
        s += """\

--- Please fill out the fields below. ---

   Package name: %(p)s
        Version: %(v)s
Upstream Author: [NAME <name@example.com>]
            URL: [http://example.com]
        License: [GPL, LGPL, BSD, MIT/X, etc.]
    Description: [DESCRIPTION]
""" % {'p': package, 'v': version}
    return s


def prepare_wnpp_subject(action, package, descr):
    if not package: 
        package = "[PACKAGE]"
    if not descr:
        descr = "[SHORT DESCRIPTION]"
    return "%s: %s -- %s" % (action, package, descr)


def getSystemInfo():
    """Returns some hopefully useful sysinfo"""
    
    s = "--- System information. ---\n"
    s += "Architecture: %s\n" % commands.getoutput("dpkg --print-installation-architecture 2>/dev/null")
    s += "Kernel:       %s\n" % commands.getoutput("uname -sr 2>/dev/null")
    
    return s


def getPackageInfo(package):
    """Returns some Info about the package."""
    
    pwidth = len("Depends ")
    vwidth = len("(Version) ")

    s = "--- Package information. ---\n"
    
    depends = getDepends(package)
    if not depends:
        return "Package depends on nothing."
    
    plist = []
    for packagestring in depends:
        split = packagestring.split(" ", 1)
        if len(split) > 1:
            depname, depversion = split
        else:
            depname = split[0]
            depversion = ""
        
        if depname.startswith("|"):
            alternative = True
            depname = depname.lstrip("|")
        
        if pwidth < len(depname):
            pwidth = len(depname)
        if vwidth < len(depversion):
            vwidth = len(depversion)
            
        plist.append(depname)
    
    instversions = getInstalledPackageVersions(plist)
    
    pwidth += len(" OR ")
    vwidth += 1

    s += "Depends".ljust(pwidth) + "(Version)".rjust(vwidth) +" | " + "Installed\n"
    s += "".zfill(pwidth).replace("0", "=")+"".zfill(vwidth).replace("0", "=")+"-+-"+"".zfill(vwidth).replace("0", "=") +"\n"
    
    alternative = False
    for packagestring in depends:
        split = packagestring.split(" ", 1)
        if len(split) > 1:
            depname, depversion = split
        else:
            depname = split[0]
            depversion = ""
        
        if depname.startswith("|"):
            alternative = True
            depname = depname.lstrip("|")
            
        if alternative:
            alternative = False
            s += (" OR "+depname).ljust(pwidth) +depversion.rjust(vwidth)+" | "+ instversions[depname] + "\n"
        else:
            s += depname.ljust(pwidth) +depversion.rjust(vwidth)+" | "+ instversions[depname] + "\n"
    
    return s


def getPackageScriptOutput(package):
    """Runs the package's script in /usr/share/bug/packagename/script and
       returns the output."""
    output = ''
    path = "/usr/share/bug/" + str(package) + "/script"
    xterm_path = "/usr/bin/x-terminal-emulator"
    # pop up a terminal if we can because scripts can be interactive
    if os.path.exists(xterm_path):
        cmd = xterm_path + " -e "
    else:
        cmd = ""
    cmd += path + " 3>&1"
    if os.path.exists(path):
        output += "--- Output from package bug script ---\n"
        output += commands.getoutput(cmd)
    return output


def getInstalledPackageVersion(package):
    """Returns the version of package, if installed or empty string if not installed"""
    
    out = commands.getoutput("dpkg --status %s 2>/dev/null" % package)
    version = re.findall("^Version:\s(.*)$", out, re.MULTILINE)
    
    if version:
        return version[0]
    else:
        return ""


def getInstalledPackageVersions(packages):
    """Returns a dictionary package:version."""
    
    result = {}
    
    packagestring = ""
    for i in packages:
        packagestring += " "+i
        result[i] = ""
    
    out = commands.getoutput("dpkg --status %s 2>/dev/null" % packagestring)
    
    packagere = re.compile("^Package:\s(.*)$", re.MULTILINE)
    versionre = re.compile("^Version:\s(.*)$", re.MULTILINE)
    
    for line in out.splitlines():
        pmatch = re.match(packagere, line)
        vmatch = re.match(versionre, line)
        
        if pmatch:
            package = pmatch.group(1)
        if vmatch:
            version = vmatch.group(1)
            result[package] = version
    
    return result


def getDepends(package):
    """Returns strings of all the packages the given package depends on. The format is like:
       ['libapt-pkg-libc6.3-6-3.11', 'libc6 (>= 2.3.6-6)', 'libstdc++6 (>= 4.1.1-12)']"""

    out = commands.getoutput("dpkg --print-avail %s 2>/dev/null" % package)
    depends = re.findall("^Depends:\s(.*)$", out, re.MULTILINE)
    
    if depends:
        depends = depends[0]
    else:
        depends = ""
    
    depends = depends.replace("| ", ", |")
    
    list = depends.split(", ")
    return list


def getSourceName(package):
    """Returns source package name for given package."""
    
    out = commands.getoutput("dpkg --print-avail %s 2>/dev/null" % package)
    source = re.findall("^Source:\s(.*)$", out, re.MULTILINE)
    
    if source:
        return source[0]
    else:
        return package


def getDebianReleaseInfo():
    """Returns a string with Debian relevant info."""
    
    debinfo = ''
    mylist = []
    output = commands.getoutput('apt-cache policy 2>/dev/null')
    if output:
        mre = re.compile('\s+(\d+)\s+.*$\s+release\s.*a=(.*?),.*$\s+origin\s(.*)$', re.MULTILINE)
        for match in mre.finditer(output):
            try:
                mylist.index(match.groups())
            except:
                mylist.append(match.groups())
    
    mylist.sort(reverse=True)

    if os.path.exists('/etc/debian_version'):
        debinfo += 'Debian Release: %s\n' % file('/etc/debian_version').readline().strip()

    for i in mylist:
        debinfo += "%+5s %-15s %s \n" % i

    return debinfo


def get_presubj(package):
    path = "/usr/share/bug/" + str(package) + "/presubj"
    if not os.path.exists(path):
        return None
    f = file(path)
    c = f.read()
    f.close()
    return c


def callBrowser(url):
    """Calls an external Browser to upen the URL."""

    # Try to find user's preferred browser via xdg-open. If that fails
    # (xdg-utils not installed or some other error), fall back to pythons
    # semi optimal solution.
    logger.debug("Just before xdg-open")
    status, output = commands.getstatusoutput('xdg-open "%s"' % url)
    logger.debug("After xdg-open")
    if status != 0:
        logger.warning("xdg-open %s returned (%i, %s), falling back to python's webbrowser.open" % (url, status, output))
        logger.debug("Just before webbrowser.open")
        thread.start_new_thread(webbrowser.open, (url,))
        logger.debug("After webbrowser.open")


def callMailClient(command):
    """
    Calls the external mailclient via command and returns the tuple:
    (status, output)
    """
    logger.debug("Just before the MUA call: %s" % str(command))
    status, output = commands.getstatusoutput(command)
    logger.debug("After the  MUA call")
    return status, output

def translate_query(query):
    """Translate query to a query the SOAP interface accepts."""

    split = query.split(':', 1)
    if (query.startswith('src:')):
        return split
    elif (query.startswith('from:')):
        return 'submitter', split[1]
    elif (query.startswith('severity:')):
        return split
    elif (query.startswith('tag:')):
        return split
    elif (query.find("@") != -1):
        return 'maint', query
    elif (re.match("^[0-9]*$", query)):
        return None, query
    else:
        return 'package', query

    
class Settings:
    
    CONFIGFILE = os.path.expanduser("~/.reportbug-ng")
    
    def __init__(self, configfile):
       
        self.configfile = configfile
        self.config = ConfigParser.ConfigParser()
        self.config.read(self.configfile)
        self.load_defaults()

        
    def load_defaults(self):
        # Users preferred mailclient
        self.lastmua = "default"
        
        self.script = True
        self.presubj = True
        
        self.c_wishlist = "#808000"
        self.c_minor = "#008000"
        self.c_normal = "#000000"
        self.c_important = "#ff0000"
        self.c_serious = "#800080"
        self.c_grave = "#800080"
        self.c_critical = "#800080"
        self.c_resolved = "#a0a0a4"
        
        # Sorting option
        self.sortByCol = 2
        self.sortAsc = False
        
        # Mainwindow
        self.x = 0
        self.y = 0
        self.width = 800
        self.height = 600
        self.menubar = True
        
        # ListView
        self.bugnrWidth = 100
        self.summaryWidth = 350
        self.statusWidth = 100
        self.severityWidth = 100
        self.lastactionWidth = 100
        
        
    def load(self):
    
        if self.config.has_option("general", "lastMUA"):
            self.lastmua = self.config.get("general", "lastMUA")
        if self.config.has_option("general", "sortByCol"):
            self.sortByCol = self.config.getint("general", "sortByCol")
        if self.config.has_option("general", "sortAsc"):
            self.sortAsc = self.config.getboolean("general", "sortAsc")

        if self.config.has_option("general", "script"):
            self.script = self.config.getboolean("general", "script")
        if self.config.has_option("general", "presubj"):
            self.presubj = self.config.getboolean("general", "presubj")
 
        if self.config.has_option("general", "wishlist"):
            self.c_wishlist = self.config.get("general", "wishlist")
        if self.config.has_option("general", "minor"):
            self.c_minor = self.config.get("general", "minor")
        if self.config.has_option("general", "normal"):
            self.c_normal = self.config.get("general", "normal")
        if self.config.has_option("general", "important"):
            self.c_important = self.config.get("general", "important")
        if self.config.has_option("general", "serious"):
            self.c_serious = self.config.get("general", "serious")
        if self.config.has_option("general", "grave"):
            self.c_grave = self.config.get("general", "grave")
        if self.config.has_option("general", "critical"):
            self.c_critical = self.config.get("general", "critical")
        if self.config.has_option("general", "resolved"):
            self.c_resolved = self.config.get("general", "resolved")
            
        if self.config.has_option("mainwindow", "x"):
            self.x = self.config.getint("mainwindow", "x")
        if self.config.has_option("mainwindow", "y"):
            self.y = self.config.getint("mainwindow", "y")
        if self.config.has_option("mainwindow", "width"):
            self.width = self.config.getint("mainwindow", "width")
        if self.config.has_option("mainwindow", "height"):
            self.height = self.config.getint("mainwindow", "height")
        if self.config.has_option("mainwindow", "menubar"):
            self.menubar = self.config.getboolean("mainwindow", "menubar")

        if self.config.has_option("listview", "bugnrwidth"):
            self.bugnrWidth = self.config.getint("listview", "bugnrwidth")
        if self.config.has_option("listview", "summarywidth"):
            self.summaryWidth = self.config.getint("listview", "summarywidth")
        if self.config.has_option("listview", "statuswidth"):
            self.statusWidth = self.config.getint("listview", "statuswidth")
        if self.config.has_option("listview", "severitywidth"):
            self.severityWidth = self.config.getint("listview", "severitywidth")
        if self.config.has_option("listview", "lastactionwidth"):
            self.lastactionWidth = self.config.getint("listview", "lastactionwidth")

    
    def save(self):

        if not self.config.has_section("general"):
            self.config.add_section("general")
        self.config.set("general", "lastMUA", self.lastmua)
        self.config.set("general", "sortByCol", self.sortByCol)
        self.config.set("general", "sortAsc", self.sortAsc)

        self.config.set("general", "script", self.script)
        self.config.set("general", "presubj", self.presubj)
        
        self.config.set("general", "wishlist", self.c_wishlist)
        self.config.set("general", "minor", self.c_minor)
        self.config.set("general", "normal", self.c_normal)
        self.config.set("general", "important", self.c_important)
        self.config.set("general", "serious", self.c_serious)
        self.config.set("general", "grave", self.c_grave)
        self.config.set("general", "critical", self.c_critical)
        self.config.set("general", "resolved",self.c_resolved)

        
        if not self.config.has_section("mainwindow"):
            self.config.add_section("mainwindow")
        self.config.set("mainwindow", "x", self.x)
        self.config.set("mainwindow", "y", self.y)
        self.config.set("mainwindow", "width", self.width)
        self.config.set("mainwindow", "height", self.height)
        self.config.set("mainwindow", "menubar", self.menubar)
        
        if not self.config.has_section("listview"):
            self.config.add_section("listview")
        self.config.set("listview", "bugnrwidth", self.bugnrWidth)
        self.config.set("listview", "summarywidth", self.summaryWidth)
        self.config.set("listview", "statuswidth", self.statusWidth)
        self.config.set("listview", "severitywidth", self.severityWidth)
        self.config.set("listview", "lastactionwidth", self.lastactionWidth)


        
        # Write everything to configfile
        self.config.write(open(self.configfile, "w"))

