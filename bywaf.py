# ---------------------------------------------------
# bywaf.py
# ---------------------------------------------------

""" Bywaf. """

# standard Python libraries
import readline
import argparse
from cmd import Cmd
import sys
import string
import concurrent.futures
import imp # for loading other modules
import os

# global constants
MAX_CONCURRENT_JOBS = 10

# history file name
HISTORY_FILENAME = "bywaf-history.txt"

# Interactive shell class
class WAFterpreter(Cmd):
    
   def __init__(self, completekey='tab', stdin=None, stdout=None):
       
      Cmd.__init__(self, completekey, stdin, stdout)
      
      # base wafterpreter constants
      self.intro = "Welcome to Bywaf"      
      self.base_prompt = "Bywaf" 
      self.set_prompt('') # set the prompt

      # currently loaded plugins, loaded & selected with he "use" command.
      # is a dictionary of { "plugin_name" : loaded_module_object }
      self.plugins = {}  
      
      # dictionary of global variable names and values
      self.global_options = {} 

      # jobs are spawned using this object's "submit()"
      self.job_executor = concurrent.futures.ProcessPoolExecutor(MAX_CONCURRENT_JOBS)      
#      self.job_executor = concurrent.futures.ThreadPoolExecutor(MAX_CONCURRENT_JOBS)

      # running counter, increments with every job; used as Job ID
      self.job_counter = 0 
      
      # job pool (list of runnign and completed Futures objects)      
      self.jobs = []  

      # currently-selected plugin's name and object (reference to a job in self.jobs)
      self.current_plugin = None
      self.current_plugin_name = ''
      
      # list of newly-finished backgrounded plugin command jobs
      self.finished_jobs = []

      
   # ----------- Overriden Methods ------------------------------------------------------
   #
   # The following methods from Cmd have been overriden to provide more functionality 
   #
   # ------------------------------------------------------------------------------------

   # override Cmd.postcmd() to notify user if a backgrounded job has completed
   def postcmd(self, stop, line):

       # if jobs just finished, then give the user notification
       if len(self.finished_jobs) > 0:
           
           for j in self.finished_jobs:
               print("[{}]  Done  {}".format(str(j.job_id), j.command_line))
           # clear the finished jobs list
           self.finished_jobs = []
           
       return stop

      
   # override Cmd.emptyline() so that it does not re-issue the last command by default
   def emptyline(self):
       return

   # override exit from command loop to say goodbye
   def postloop(self):
        print('Goodbye')
        
   # override Cmd.getnames() to return dir(), and not 
   # dir(self.__class__).  Otherwise, get_names() doesn't return the
   # names of dynamically-added do_* commands 
   def get_names(self):
       return dir(self) 
           
   # override completenames() to give an extra space (+' ') for completed command names
   # and to better matches bash's completion behavior 
   def completenames(self, text, *ignored):
        dotext = 'do_'+text
        return [a[3:]+' ' for a in self.get_names() if a.startswith(dotext)]

   # override Cmd.onecmd() to enable user to background a task
   def onecmd(self, _line):
 
        # flag variable
        exec_in_background = False
        
        line = _line.strip()        
        
        # ignore comment lines
        if line.startswith('#'):
            return
        
        # if the user only specified a number, then show the results of that backgrounded task
        if line.isdigit():
            self.do_result(line)
            return
        
        # set the backgrounding flag if the line ends with &
        if line.endswith('&'):
            exec_in_background = True
            line = _line[:-1]

        # extract command and its arguments from the line
        cmd, arg, line = self.parseline(line)
        
        self.lastcmd = line        
        
        # if the line is blank, return self.emptyline()
        if not cmd:
            return self.emptyline()
        
        # quit on EOF - get this working
        elif cmd in ['EOF', 'quit', 'exit']:
            self.lastcmd = ''
            return 1

        # else, process thie command
        else:
            
            try:
                func = getattr(self, 'do_' + cmd)
            except AttributeError:
                print('command "{}" not found'.format(cmd))
                return # return self.default(line)
      
            # list of commands for the currently-selected plugin
            command_names = []
            if self.current_plugin: 
                command_names = self.current_plugin.commands

            # if user requested it, background the job
            # do not do this for internal commands                
            if exec_in_background: #and self.current_plugin and cmd in command_names:
               
                print('backgrounding job {}'.format(self.job_counter))
                
                # background the job
                job = self.job_executor.submit(func, arg)
                
                job.job_id = self.job_counter
                job.name = self.current_plugin_name + '/' + cmd
                job.command_line = line
                job.add_done_callback(self.finished_job_callback)

                # add job to the list of running jobs
                self.jobs.append(job)
                self.job_counter += 1                
                
                ret = 0 # 0 keeps WAFterpreter going, 1 quits it

            # else, just run the job (returning 1 causes Bywaf to exit)
            else:
                
                func(arg)                
                ret = 0 # 0 keeps WAFterpreter going, 1 quits it 
                
            return ret
    
   # ----------- API and Utility Methods ----------------------------------------------
   #
   # These methods are exposed to the plugins and may be overriden by them.
   #
   #-----------------------------------------------------------------------------------   
      
   # utility method to autocomplete filenames.
   # Code adapted from http://stackoverflow.com/questions/16826172/filename-tab-completion-in-cmd-cmd-of-python
   
   # I added "level", which is the level of command at which text is being completed.
   # level 1:  >command te<tab>   <-- text being completed here
   # level 2:  >command subcommand te<tab>  <-- text being completed here
   def filename_completer(self, text, line, begidx, endidx, level=1):

      arg = line.split()[level:]
      
      if not arg:
          completions = os.listdir('./')
      else:
          dir, part, base = arg[-1].rpartition('/')
          if part == '':
              dir = './'
          elif dir == '':
              dir = '/'            
              
          completions = []
          for f in os.listdir(dir):
              if f.startswith(base):
                  if os.path.isfile(os.path.join(dir,f)):
                      completions.append(f)
                  else:
                      completions.append(f+'/')
                      
      return completions
  
   # set an option's value.  Called by do_set()            
   def set_option(self, name, value):

       # retrieve the option (it's a tuple)       
       _value, _defaultvalue, _required, _descr = self.current_plugin.options[name]
       
       # construct a new option tuple and set the option to it       
       self.current_plugin.options[name] = value, _defaultvalue, _required, _descr
       
   # return a Futures object given its job ID as a string or int
   def get_job(self, _job_id):

       job = None 
       job_id = int(_job_id) 

       # build a dict of jobs indexed by job_id
       jobs = dict(zip((j.job_id for j in self.jobs), (j for j in self.jobs)))

       # try and return the job, None if it was not there
       try:
           job = jobs[job_id]
       except KeyError:
           pass
       
       return job
   
   # update list of newly-finished jobs 
   def finished_job_callback(self, finished_job):
       self.finished_jobs.append(finished_job)
       
   # physically load a module (called from do_import)
   # implementation adapted from http://stackoverflow.com/questions/301134/dynamic-module-import-in-python   
   def _load_module(self, filepath):
       
       py_mod = None
       
       # extract module's name and extension from filepath
       mod_name,file_ext = os.path.splitext(os.path.split(filepath)[-1])
       
       # if it is a precompiled module, use the precompiled loader
       if file_ext.lower() == '.pyc':
           try:
               py_mod = imp.load_compiled(mod_name, filepath)
           except Exception as e:
               raise Exception('Could not load precompiled plugin module: {}'.format(e))
           
       # else just try to load it with the standard loader
       else:
           
           py_mod = imp.load_source(mod_name, filepath)
           try:
               pass
           except Exception as e:
               raise Exception('Could not load plugin module: {}'.format(e))
                                              
       # verify that this module has the necessary Bywaf infrastructure
       if not hasattr(py_mod, "options"):
           raise Exception("options dictionary not found")
           
       # return the loaded module
       return mod_name, py_mod
   
   # set the prompt to the given plugin name
   def set_prompt(self, plugin_name):
       try:   # can fail if no plugin is loaded (i.e. plugin_name=="")
           self.prompt = self.base_prompt + '/' + plugin_name + '>'           
       except:
           self.prompt = self.base_prompt + '>'           
           
   # retrieve command history
   # code adapted from pymotw.com/2/readline/
   def get_history_items(self):
       return [ readline.get_history_item(i)
                for i in xrange(1, readline.get_current_history_length() + 1)
                ]

   # try to write history to disk.  It is the caller's responsibility to handle exceptions
   def save_history(self, filename):
       readline.write_history_file(filename)

   # read history in, if it exists  It is the caller's responsibility to handle exceptions                
   def load_history(self, filename):
       readline.read_history_file(filename)

   # clear command history
   def clear_history(self):
       readline.clear_history()


   # ----------- Command & Command Completion methods ------------------------------------------       
        
   
   # load plugin module given its file path, and set it as the current plugin
   def do_use(self, _filepath):
       """Load a module given the module path"""

       filepath = _filepath.strip()
       
       try:
           new_module_name, new_module = self._load_module(filepath)
       except Exception as e:
           print('Could not load module {}: {}'.format(filepath,e))
           return

       # if this plugin has already been loaded, notify user.
       # this will revert any changes they made to the options
       if self.current_plugin_name == new_module_name:
           print('Import:  Overwriting already loaded module "{}"'.format(new_module_name))

       # give the new module access to other modules
       new_module.app = self           
           
       # remove currently selected plugin's functions from the Cmd command list
       if self.current_plugin:
           for _command in self.current_plugin.commands:
               command = _command.__name__
               if hasattr(self, command):  delattr(self, command)
               if hasattr(self, 'help_'+command):  delattr(self, 'help_'+command[5:])
               if hasattr(self, 'complete_'+command):  delattr(self, 'complete_'+command[10:])

       # register with our list of modules (i.e., insert into our dictionary of modules)
       self.plugins[new_module_name] = new_module
       
       
       commands = [f for f in dir(new_module) if f.startswith('do_')]
       self.plugins[new_module_name].commands = commands
       
       # set current plugin
       # and change the prompt to reflect the plugin's name
       self.set_prompt(new_module_name)
       self.current_plugin_name = new_module_name
       self.current_plugin = new_module
       
       # add module's functions to the Cmd command list
       for command_name in new_module.commands:
           
           # register the command 
           # it is a tuple of the form (function, string)
           command_func = getattr(new_module, command_name)
           setattr(self, command_name, command_func)

           # try and register its optional help function, if one exists
           try:
               helpfunc = getattr(new_module, 'help_' + command_name[5:])
               setattr(self, 'help' + command_name, helpfunc)
           except:
               pass 
               
           # try and register its optional completion function, if one exists
           try:
               completefunc = getattr(new_module, 'complete_' + command_name[10:])
               setattr(self, 'complete_' + command_name, completefunc)
           except:
               pass
           
   # alias use()'s completion function to the filename completer
   complete_use = filename_completer 

   # attempt to cancel a running job
   def do_kill(self, args):
       """cancel one or more running jobs"""
       
       try:
           job_ids = [int(i) for i in args.split()]
       except:
           print('usage: kill <JOB> [<JOB2> ...  <JOBN>]')
           return

       # loop over the specified jobs...
       for job_id in job_ids:
         job = self.get_job( job_id )
         
         # ...and try to end them
         try:
             job.cancel()
         except:
             print('Job ID {} not found'.format(job_id))

             
   def complete_kill(self,text,line,begin_idx,end_idx):
       job_ids  = [str(j.job_id) for j in self.jobs if not j.done()]
       opts = [x+' ' for x in job_ids if x.startswith(text)]
       return opts                                     
             

   def do_d(self, args):
       """remove one or more completed jobs from the jobs queue"""

       try:
           job_ids = [int(i) for i in args.split()]
       except:
           print('usage: d <JOB> [<JOB2> ...  <JOBN>]')
           return

       for job_id in job_ids:
         # loop through jobs list looking for a job with a matching job_id ,
         for (i,item) in enumerate(self.jobs):
           
             # match found, so remove it.  Fail if this job is currently running.
             if item.job_id == job_id:
                 if item.running():
                     print('Job {} is still running!'.format(job_id))
                     break

                 # remove from job queue
                 del self.jobs[i]
                 break
         else:
             print('Job ID {} not found'.format(job_id))

   # completion function for the kill command: return only running jobs
   def complete_d(self,text,line,begin_idx,end_idx):
       job_ids  = [str(j.job_id) for j in self.jobs if j.done()]
       opts = [x+' ' for x in job_ids if x.startswith(text)]
       return opts                        
           
   def do_result(self, _job_id):
       """show the result of a job given its ID number"""
       
       try:
           job_id = int(_job_id)
       except:
           print('usage: result <JOBID> or just <JOBID>')
           return
       
       jobs = dict(zip((j.job_id for j in self.jobs), (j for j in self.jobs)))

       # verify that job ID is valid
       if job_id in jobs.keys():
           
           job = jobs[job_id]

           # print job result if it is available, else notify user and return empty
           if job.running():
               print('Job {} still running'.format(job_id))
               return

           # else return the job's result
           else:
               result_text =  job.result()
               print(result_text)
           
       # if job ID is not valid, print error and return
       else:
           print('Job ID {} not found'.format(job_id))
           
   # completion function for the do_result command: return only completed jobs
   def complete_result(self,text,line,begin_idx,end_idx):
       job_ids  = [str(j.job_id) for j in self.jobs if j.done()]
       opts = [x+' ' for x in job_ids if x.startswith(text)]
       return opts                                   

   def do_script(self, scriptfilename):
       """Load a script file"""
       try:
           with open(scriptfilename) as scriptfile:

               # loop over every input lines...
               for line in scriptfile:
                   # ...adding it to the command queue in turn.  
                   # This is a more elegant appraoch than calling self.onecmd())
                   print('Appending line "{}"'.format(line))
                   self.cmdqueue.append(line)
                   
       except IOError as e: 
           print('Could not load script file: {}'.format(e))
           
   # alias script()'s completion function to the filename completer
   complete_script = filename_completer
   
   def do_jobs(self, args):
       """list the status of running and completed jobs"""
       
       # total number of jobs in the queue or completed
       total_jobs = len(self.jobs)
       
       # return if there is nothing to show
       if total_jobs == 0:
           print('No jobs completed or currently running.')
           return
       
       # loop over futures objects and tally results
       jobs_completed = len([j for j in self.jobs if j.done()])
       print('{} jobs total:  {} complete, {} running\n'.format(total_jobs, jobs_completed, total_jobs-jobs_completed))
       
       # construct the format string:  left-aligned, space-padded, minimum.maximum
       format_string = "{:<4.4} {:<20.20} {:<15.15}"
       
       # print the header
       print(format_string.format("ID", "Command", "Status"))
       print(format_string.format(*["-"*20]*3))
       
       # loop through the jobs and display each
       for j in self.jobs:
           status = ''
           if j.done():
               status = 'Completed'
           elif j.running():
               status = 'Running'
           elif j.paused():
               status = 'Paused'  # not sure if this is reached
           print(format_string.format( str(j.job_id), j.command_line, status ))
        
   def do_gset(self, args):
       """set a global variable.  This command takes the form 'gset VARNAME VALUE'."""

       (key,value)=string.split(args, maxsplit=1)
       self.global_options[key] = value
       
       print('{} => {}'.format(key, value))
       
   # completion function for the do_gset command: return available global option names
   def complete_gset(self,text,line,begin_idx,end_idx):
       option_names = [opt+' ' for opt in self.global_options.keys() if opt.startswith(text)]
       return option_names       
       
   def do_gshow(self, args):
       """Show global variables."""
       
       # construct the format string:  function name, description
       format_string = '{:<20.20} {}'
       
       # print the header
       print(format_string.format('Global Option', 'Value'))
       print(format_string.format(*["-"*20] * 2))

       for k in sorted(self.global_options.keys()): 
           print(format_string.format(k, self.global_options[k]))

   # completion function for the do_gset command: return available global option names
   def complete_gshow(self,text,line,begin_idx,end_idx):
       option_names = [opt+' ' for opt in self.global_options.keys() if opt.startswith(text)]
       return option_names                  
           
   def do_set(self, arg):
       """set a plugin's local variable.  This command takes the form 'set VARNAME VALUE'."""

       # if no plugin is currently selected       
       if not self.current_plugin:
           print('no plugin currently selected')
           return

       (name,value)=string.split(arg, maxsplit=1)

       self.set_option(name, value)

       print('{} => {}'.format(name, value))

   # completion function for the do_set command: return available option names
   def complete_set(self,text,line,begin_idx,end_idx):
       option_names = [opt+' ' for opt in self.current_plugin.options.keys() if opt.startswith(text)]
       return option_names
       
   def do_show(self, args):
       """display local vars for this plugin"""

       # if no plugin is currently selected
       if not self.current_plugin: 
           print('No plugin currently selected')
           return

       optname = args.strip()
       available_options = self.current_plugin.options.keys()
       option_list = []

       # default to show all option names.  If a user specifies a specific option, then only show that one
       if not optname:
           option_list = available_options
           
       elif optname not in option_list:
           print('{}: no such plugin option'.format(optname))
           return
           
       else:
           option_list = [optname]
       

       # construct the format string:  left-aligned, space-padded, minimum.maximum
       format_string = '{:<15.15} {:<15.15} {:<15.15} {:<15.15} {:<15.30}'
       
       # print the header
       print('\n\n')
       print(format_string.format('Option', 'Value', 'Default Value', 'Required', 'Description'))
       print(format_string.format(*["-"*15] * 5))

       # loop through the plugin's available options and display them
       for name in sorted(option_list): 
           value, defaultvalue, required, description = self.current_plugin.options[name]
           print(format_string.format(name, value, defaultvalue, required, description))
           
           
       # ------ show this plugin's available commands -----------
       
       # construct the format string:  function name, description
       format_string = '{:<20.20} {}'
       
       # print the header
       print('\n\n')
       print(format_string.format('Command', 'Description'))
       print(format_string.format(*["-"*20] * 2))

       # loop through the plugin's available options and display them
       for command_name in sorted(self.current_plugin.commands):
           command_docstring = getattr(self.current_plugin, command_name).__doc__
           print(format_string.format(command_name[3:], command_docstring))
           
   # completion function for the do_set command: return available option names
   def complete_show(self,text,line,begin_idx,end_idx):
       option_names = [opt+' ' for opt in self.current_plugin.options.keys() if opt.startswith(text)]
       return option_names           

   def do_shell(self, line):
       """Execute shell commands"""
       output = os.popen(line).read()
       print(output)
       
   def do_history(self, params):
       """Load, save, display and clear command history"""
       
       cmd = params.split()
       
       if cmd[0]=='load':
           try:
               fname = cmd[1]
               self.load_history(fname)
           except IndexError: # no filename specified
               print('filename not specified')
           except IOError as e: # error in loading file
               print('could not load file: {}'.format(e))
               
       elif cmd[0]=='save':
           try:
               fname = cmd[1]
               self.save_history(fname)
           except IndexError: # no filename specified
               print('filename not specified')
           except IOError as e: # error in saving file
               print('could not write file: {}'.format(e))

       elif cmd[0]=='show':
           print('\n'.join(self.get_history_items()))
           
       elif cmd[0]=='clear':
           self.clear_history()
       
   # completion function for the do_history command:  two-level completion (subcommand, then filename)
   def complete_history(self,text,line,begin_idx,end_idx):
       
       words = line.split()

       # find out if this is first- or second-level completion:
       
       # first level completion: we see either one word, OR two words and the second is only partially completed.  Complete the subcommand.
       if len(words)==1 or (len(words)==2 and words[1] not in ['load', 'save', 'show', 'clear']):
           option_names = [opt+' ' for opt in ['load', 'save', 'show', 'clear'] if opt.startswith(text)]
           return option_names
           
       # second level completion: we see two words, the second fully completed. Complete the filename.
       else:
           
           # note: only complete for "load" and "save" subcommands'
           if words[1] not in ['load', 'save']:
               return
 
           # re-use the filename completer
           return self.filename_completer(text, line, begin_idx, end_idx, level=2)           



#---------------------------------------------------------------
#
# Main function
#
#---------------------------------------------------------------
       

if __name__=='__main__':

    # parse arguments
    parser = argparse.ArgumentParser(description='Bypass web application firewalls')
    parser.add_argument('--input', dest='inputfilename', action='store', help='read input from a file')
    parser.add_argument('--script', dest='scriptfilename', action='store', help='execute a script and stay in wafterpreter')
    parser.add_argument('--out', dest='outfilename', action='store', help='redirect output to a file')
    args = parser.parse_args()

    # assign default input and output streams
    input = sys.stdin
    output = sys.stdout

    # set the input and output streams according to the user's request
    if args.inputfilename:
        try:
            input = open(args.inputfilename, 'rt')
        except IOError as e:
            print('Could not open input file: {}'.format(e))
            sys.exit(1)

    if args.outfilename:
        try:
            output = open(args.outfilename, 'rt')
        except IOError as e:
            print('Could not open output file: {}'.format(e))
            sys.exit(2)
        

    # initialize command interpreter 
    wafterpreter = WAFterpreter(stdin=input, stdout=output)
    
    # automatically read history in, if it exists
    try:
        wafterpreter.load_history(HISTORY_FILENAME)
    except IOError:
        pass
    
    # execute a script if the user specified one
    if args.scriptfilename:
        try:
            wafterpreter.do_script(args.scriptfilename)                                
        except IOError as e:
            print('Could not open script file: {}'.format(e))
            sys.exit(3)

    # begin accepting commands
    wafterpreter.cmdloop()
