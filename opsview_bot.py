#! /usr/bin/env python
#
# Example program using ircbot.py.
#
# Joel Rosdahl <joel@rosdahl.net>

from ircbot import SingleServerIRCBot
from irclib import nm_to_n, nm_to_h, irc_lower, ip_numstr_to_quad, ip_quad_to_numstr
import opsview
import datetime

class OpsviewBot(SingleServerIRCBot):
    def __init__(self, options):
        #super(OpsviewBot, self).__init__(
        SingleServerIRCBot.__init__(self,
            [(options.server, options.port)],
            options.nickname,
            options.nickname
        )
        self.channel = options.channel
        self.ops_server = opsview.OpsviewServer(
            base_url=options.base_url,
            username=options.username,
            password=options.password
        )

        self.connection.execute_delayed(
            delay=15,
            function=self.output_status,
            arguments=(True,)
        )
        self.alerting = []
        if options.log_file != '':
            self._log('Logging to: %s' % options.log_file)
            self._log_file = open(options.log_file, 'a+')
            
    def __del__(self):
        try:
            self._log_file.flush()
            self._log_file.close()
        except AttributeError:
            pass
        
    def _log(self, message):
        log_message = '%s LOG: %s' % (datetime.datetime.now().isoformat(), message)
        print log_message
        try:
            self._log_file.write(log_message + '\n')
        except AttributeError:
            pass

    def on_nicknameinuse(self, c, e):
        self._log('Nickname in use, appending underscore')
        c.nick(c.get_nickname() + "_")

    def on_welcome(self, c, e):
        self._log('Joining channel: %s' % self.channel)
        c.join(self.channel)

    def on_privmsg(self, c, e):
        self.do_command(e, e.arguments()[0])

    def on_pubmsg(self, c, e):
        a = e.arguments()[0].split(":", 1)
        if len(a) > 1 and irc_lower(a[0]) == irc_lower(self.connection.get_nickname()):
            self.do_command(e, a[1].strip())
        return

    def do_command(self, event, cmd):
        nick = nm_to_n(event.source())
        cmd = cmd.split(' ')
        self._log('%s sent command/args: %s' % (nick,cmd))
        try:
            cmd, cmd_args = cmd[0], cmd[1:]
        except IndexError:
            pass

        if cmd == "disconnect":
            self.disconnect()
        elif cmd == "die":
            self.die()
        elif cmd == "ack":
            self._log('Trying ack: %s' % cmd_args)
            try:
                if cmd_args[0] in [host['name'] for host in self.ops_server.children]:
                    if len(cmd_args) > 2 and cmd_args[0]+cmd_args[1] in [host['name']+service['name']
                                                                         for host in self.ops_server.children for service in host.children]:
                        self._log('Acking service: %s %s' % (cmd_args[0], cmd_args[1]))
                        self.ops_server.remote.acknowledge_service(
                            host=cmd_args[0],
                            service=cmd_args[1],
                            comment='%s via IRC: %s' % (nick, ' '.join(cmd_args[2:])),
                        )
                        self.connection.notice(self.channel, 'Acknowledged service %s:%s for %s' %
                                               (cmd_args[0], cmd_args[1], nick))
                    else:
                        self._log('Acking host: %s' % cmd_args[0])
                        self.ops_server.remote.acknowledge_host(
                            host=cmd_args[0],
                            comment='%s via IRC: %s' % (nick, ' '.join(cmd_args[1:])),
                        )
                        self.connection.notice(self.channel, 'Acknowledged host %s for %s' % (cmd_args[0], nick))
                else:
                    self._log('Acking all')
                    self.ops_server.remote.acknowledge_all(
                        comment='%s via IRC: %s' % (nick, ' '.join(cmd_args)))
                    self.connection.notice(self.channel, 'Acknowledged all for %s' % nick)
            except Exception, error:
                self._log('Error acking: %s' % error)
                self.connection.notice(self.channel, 'Uncaught exception while acknowledging: %s' % error)
        elif cmd == "status":
            self._log('Sending status')
            self.connection.notice(self.channel, 'Currently alerting: ' + ', '.join(self.alerting))
        else:
            self.connection.notice(nick, "Not understood: " + cmd)

    def output_status(self, circular=False):
        max_state_duration = 12 * 60 * 60 # hours x minutes x seconds
        now_alerting = []
        try:
            self.ops_server.update([opsview.STATE_WARNING, opsview.STATE_CRITICAL])
        except opsview.OpsviewException, error:
            self._log('Error updating opsview data: %s' % error)
            self.connection.notice(self.channel, 'Error: %s' % error)
            self.connection.execute_delayed(delay=15, function=self.output_status,arguments=(True,))
        else:
            for host in self.ops_server.children:
                if host['state'] == opsview.STATE_DOWN and \
                   host['current_check_attempt'] == host['max_check_attempts'] and \
                   host['state_duration'] < max_state_duration:
                    now_alerting.append('%s:%s' % (host['name'], host['state']))
                else:
                    for service in host.children:
                        if service['current_check_attempt'] == service['max_check_attempts'] and \
                           service['state_duration'] < max_state_duration and \
                           'flapping' not in service:
                            now_alerting.append('%s[%s]:%s' % (host['name'], service['name'], service['state']))
            new_alerting = filter(lambda hash: hash not in self.alerting, now_alerting)
            recovered = filter(lambda hash: hash not in now_alerting, self.alerting)
            self.alerting = now_alerting
            new_alerting = ', '.join(new_alerting)
            recovered = ', '.join(recovered)
            if len(recovered) is not 0:
                self._log('Recoveries: %s' % recovered)
                self.connection.notice(self.channel, 'Recovered: ' + recovered)
            if len(new_alerting) is not 0:
                self._log('New alerts: %s' % new_alerting)
                self.connection.notice(self.channel, 'New alerts: ' + new_alerting)
            self.connection.execute_delayed(delay=15, function=self.output_status,arguments=(True,))


if __name__ == "__main__":
    def main():
        from optparse import OptionParser
        from os.path import basename
        import sys

        parser = OptionParser(
            usage='%prog [options]',
            version='%prog v0.2.0'
        )

        required_options = [
            'base_url',
            'channel',
            'server',
            'username',
            'password',
        ]

        parser.add_option('-b', '--base-url',
                          type='string', metavar='URL',
                          help='Required. Base URL to the Opsview Server.')
        parser.add_option('-c', '--channel',
                          type='string', metavar='#CHANNEL',
                          help='Required.')
        parser.add_option('-n', '--nickname',
                          type='string', default='opsbot')
        parser.add_option('-p', '--port',
                          type='int', default=6667)
        parser.add_option('-s', '--server',
                          type='string', metavar='IP.OR.HOST.NAME',
                          help='Required. IRC server to connect to.')
        parser.add_option('-u', '--username',
                          type='string', help='Required. Opsview username.')
        parser.add_option('-w', '--password',
                          type='string', help='Required. Opsview user\'s password.')
        parser.add_option('-l', '--log-file',
                          type='string', help='Optional. Log to a file.',
                          default='', metavar='FILE')

        options, _ = parser.parse_args()

        missing_options = filter(
            lambda option: not hasattr(options, option),
            required_options
        )
        if missing_options:
            print 'Missing required options: %s' % missing_options
        else:
            bot = OpsviewBot(options=options)
            bot.start()

    main()