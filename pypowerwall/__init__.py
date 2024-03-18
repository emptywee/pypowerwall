# pyPowerWall Module
# -*- coding: utf-8 -*-
"""
 Python module to interface with Tesla Solar Powerwall Gateway

 Author: Jason A. Cox
 For more information see https://github.com/jasonacox/pypowerwall

 Features
    * Works with Tesla Energy Gateways - Powerwall+
    * Simple access through easy to use functions using customer credentials
    * Will cache authentication to reduce load on Powerwall Gateway
    * Will cache responses for 5s to limit number of calls to Powerwall Gateway
    * Will re-use http connections to Powerwall Gateway for reduced load and faster response times
    * Can use Tesla Cloud API instead of local Powerwall Gateway (if enabled)
    * Uses Auth Cookie or Bearer Token for authorization (configurable)

 Classes
    Powerwall(host, password, email, timezone, pwcacheexpire, timeout, poolmaxsize, 
        cloudmode, siteid, authpath, authmode)

 Parameters
    host                      # Hostname or IP of the Tesla gateway
    password                  # Customer password for gateway
    email                     # (required) Customer email for gateway / cloud
    timezone                  # Desired timezone
    pwcacheexpire = 5         # Set API cache timeout in seconds
    timeout = 5               # Timeout for HTTPS calls in seconds
    poolmaxsize = 10          # Pool max size for http connection re-use (persistent
                                connections disabled if zero)
    cloudmode = False         # If True, use Tesla cloud for data (default is False)
    siteid = None             # If cloudmode is True, use this siteid (default is None)
    authpath = ""             # Path to cloud auth and site files (default current directory)
    authmode = "cookie"       # "cookie" (default) or "token" - use cookie or bearer token for auth
    cachefile = ".powerwall"  # Path to cache file (default current directory)
    
 Functions 
    poll(api, json, force)    # Return data from Powerwall api (dict if json=True, bypass cache force=True)
    level()                   # Return battery power level percentage
    power()                   # Return power data returned as dictionary
    site(verbose)             # Return site sensor data (W or raw JSON if verbose=True)
    solar(verbose):           # Return solar sensor data (W or raw JSON if verbose=True)
    battery(verbose):         # Return battery sensor data (W or raw JSON if verbose=True)
    load(verbose)             # Return load sensor data (W or raw JSON if verbose=True)
    grid()                    # Alias for site()
    home()                    # Alias for load()
    vitals(json)              # Return Powerwall device vitals (dict or json if True)
    strings(json, verbose)    # Return solar panel string data
    din()                     # Return DIN
    uptime()                  # Return uptime - string hms format
    version()                 # Return system version
    status(param)             # Return status (JSON) or individual param
    site_name()               # Return site name
    temps()                   # Return Powerwall Temperatures
    alerts()                  # Return array of Alerts from devices
    system_status(json)       # Returns the system status
    battery_blocks(json)      # Returns battery specific information merged from system_status() and vitals()
    grid_status(type)         # Return the power grid status, type ="string" (default), "json", or "numeric"
                              #     - "string": "UP", "DOWN", "SYNCING"
                              #     - "numeric": -1 (Syncing), 0 (DOWN), 1 (UP)
    is_connected()            # Returns True if able to connect and login to Powerwall
    get_reserve(scale)        # Get Battery Reserve Percentage
    get_time_remaining()      # Get the backup time remaining on the battery

 Requirements
    This module requires the following modules: requests, protobuf, teslapy
    pip install requests protobuf teslapy
"""
import json
import logging
import os.path
import sys
from typing import Union, Optional

# noinspection PyPackageRequirements
import urllib3

from cloud.pypowerwall_cloud import PyPowerwallCloud
from local.pypowerwall_local import PyPowerwallLocal
from pypowerwall_base import parse_version, PyPowerwallBase

urllib3.disable_warnings()  # Disable SSL warnings

version_tuple = (0, 8, 0)
version = __version__ = '%d.%d.%d' % version_tuple
__author__ = 'jasonacox'

log = logging.getLogger(__name__)
log.debug('%s version %s', __name__, __version__)
log.debug('Python %s on %s', sys.version, sys.platform)


def set_debug(toggle=True, color=True):
    """Enable verbose logging"""
    if toggle:
        if color:
            logging.basicConfig(format='\x1b[31;1m%(levelname)s:%(message)s\x1b[0m', level=logging.DEBUG)
        else:
            logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
        log.setLevel(logging.DEBUG)
        log.debug("%s [%s]\n" % (__name__, __version__))
    else:
        log.setLevel(logging.NOTSET)


class Powerwall(object):
    def __init__(self, host="", password="", email="nobody@nowhere.com",
                 timezone="America/Los_Angeles", pwcacheexpire=5, timeout=5, poolmaxsize=10,
                 cloudmode=False, siteid=None, authpath="", authmode="cookie", cachefile=".powerwall"):
        """
        Represents a Tesla Energy Gateway Powerwall device.

        Args:
            host        = Hostname or IP address of Powerwall (e.g. 10.0.1.99)
            password    = Customer password set up on Powerwall gateway
            email       = Customer email 
            timezone    = Timezone for location of Powerwall 
                (see https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) 
            pwcacheexpire = Seconds to expire cached entries
            timeout      = Seconds for the timeout on http requests
            poolmaxsize  = Pool max size for http connection re-use (persistent connections disabled if zero)
            cloudmode    = If True, use Tesla cloud for data (default is False)
            siteid       = If cloudmode is True, use this siteid (default is None)  
            authpath     = Path to cloud auth and site cache files (default current directory)
            authmode     = "cookie" (default) or "token" - use cookie or bearer token for authorization
            cachefile    = Path to cache file (default current directory)
        """

        # Attributes
        self.cachefile = cachefile  # Stores auth session information
        self.host = host
        self.password = password
        self.email = email
        self.timezone = timezone
        self.timeout = timeout  # 5s timeout for http calls
        self.poolmaxsize = poolmaxsize  # pool max size for http connection re-use
        self.auth = {}  # caches auth cookies
        self.token = None  # caches bearer token
        self.pwcachetime = {}  # holds the cached data timestamps for api
        self.pwcache = {}  # holds the cached data for api
        self.pwcacheexpire = pwcacheexpire  # seconds to expire cache
        self.cloudmode = cloudmode  # cloud mode or local mode (default)
        self.siteid = siteid  # siteid for cloud mode
        self.authpath = os.path.expanduser(authpath)  # path to auth and site cache files
        self.authmode = authmode  # cookie or token
        self.pwcooldown = 0  # rate limit cooldown time - pause api calls
        self.vitals_api = True  # vitals api is available for local mode
        self.client: PyPowerwallBase
        # Check for cloud mode
        if self.cloudmode or self.host == "":
            self.cloudmode = True
            self.client = PyPowerwallCloud(self.email, self.pwcacheexpire, self.timeout, self.siteid, self.authpath)
            # Check to see if we can connect to the cloud
        else:
            self.cloudmode = False
            self.client = PyPowerwallLocal(self.host, self.password, self.email, self.timezone, self.timeout,
                                           self.pwcacheexpire, self.poolmaxsize, self.authmode, self.cachefile)

        self.client.authenticate()

    def is_connected(self):
        """
        Attempt connection with Tesla Energy Gateway
        
        Return True if able to successfully connect and login to Powerwall
        """
        # noinspection PyBroadException
        try:
            if self.status() is None:
                return False
            return True
        except Exception:
            return False

    def poll(self, api='/api/site_info/site_name', jsonformat=False, raw=False, recursive=False,
             force=False) -> Union[dict, str]:
        """
        Query Tesla Energy Gateway Powerwall for API Response
        
        Args:
            api         = URI 
            jsonformat  = If True, return JSON format otherwise return Python Dictionary
            raw         = If True, send raw data back (useful for binary responses, has no meaning in Cloud mode)
            recursive   = If True, this is a recursive call and do not allow additional recursive calls
            force       = If True, bypass the cache and make the API call to the gateway, has no meaning in Cloud mode
        """
        # Check to see if we are in cloud mode
        payload = self.client.poll(api, force, recursive, raw)
        if jsonformat:
            return json.dumps(payload)
        else:
            return payload

    def level(self, scale=False):
        """ 
        Battery Level Percentage 
            Note: Tesla App reserves 5% of battery => ( (batterylevel / 0.95) - (5 / 0.95) )
        Args:
            scale = If True, convert battery level to app scale value
        """
        # Return power level percentage for battery
        payload = self.poll('/api/system_status/soe')
        if payload is not None and 'percentage' in payload:
            level = payload['percentage']
            if scale:
                level = (level / 0.95) - (5 / 0.95)
            return level
        return None

    def power(self) -> dict:
        """
        Power Usage for Site, Solar, Battery and Load
        """
        # Return power for (site, solar, battery, load) as dictionary
        return self.client.power()

    def vitals(self, jsonformat=False):
        """
        Device Vitals Data

        Args:
           jsonformat = If True, return JSON format otherwise return Python Dictionary
        """
        output = self.client.vitals()

        # Return result
        if jsonformat:
            json_out = json.dumps(output, indent=4, sort_keys=True)
            return json_out
        else:
            return output

    def strings(self, jsonformat=False, verbose=False):
        """
        Solar Strings Data (current, voltage, power, state, connected)

        Args:
           jsonformat = If True, return JSON format otherwise return Python Dictionary
           verbose    = If True, return all String data details otherwise basics
        """
        result = {}
        devicemap = ['', '1', '2', '3', '4', '5', '6', '7', '8']
        deviceidx = 0
        v: dict = self.vitals() or {}
        for device in v:
            if device.split('--')[0] == 'PVAC':
                # Check for PVS data
                look = "PVS" + str(device)[4:]
                if look in v:
                    # Inject the PVS string data into the dictionary
                    for ee in v[look]:
                        if 'String' in ee:
                            v[device][ee] = v[look][ee]
                if verbose:
                    result[device] = {}
                    result[device]['PVAC_Pout'] = v[device]['PVAC_Pout']
                    for e in v[device]:
                        if 'PVAC_PVCurrent' in e or 'PVAC_PVMeasuredPower' in e or \
                                'PVAC_PVMeasuredVoltage' in e or 'PVAC_PvState' in e or \
                                'PVS_String' in e:
                            result[device][e] = v[device][e]
                else:  # simplified results
                    for e in v[device]:
                        if 'PVAC_PVCurrent' in e or 'PVAC_PVMeasuredPower' in e or \
                                'PVAC_PVMeasuredVoltage' in e or 'PVAC_PvState' in e or \
                                'PVS_String' in e:
                            name = e[-1] + devicemap[deviceidx]
                            if 'Current' in e:
                                idxname = 'Current'
                            elif 'Power' in e:
                                idxname = 'Power'
                            elif 'Voltage' in e:
                                idxname = 'Voltage'
                            elif 'State' in e:
                                idxname = 'State'
                            elif 'Connected' in e:
                                idxname = 'Connected'
                                name = e[10] + devicemap[deviceidx]
                            else:
                                idxname = 'Unknown'
                            if name not in result:
                                result[name] = {}
                            result[name][idxname] = v[device][e]
                        # if
                    # for   
                    deviceidx += 1
                # else
        # If no devices found pull from /api/solar_powerwall
        if not v:
            # Build a string map: A, B, C, D, A1, B2, etc.
            string_map = []
            for number in ['', '1', '2', '3', '4', '5', '6', '7', '8']:
                for letter in ['A', 'B', 'C', 'D']:
                    string_map.append(letter + number)
            payload: dict = self.poll('/api/solar_powerwall') or {}
            if payload and 'pvac_status' in payload:
                # Strings are in PVAC status section
                pvac = payload['pvac_status']
                if 'string_vitals' in pvac:
                    i = 0
                    for string in pvac['string_vitals']:
                        name = string_map[i]
                        result[name] = {}
                        result[name]['Connected'] = string['connected']
                        result[name]['Voltage'] = string['measured_voltage']
                        result[name]['Current'] = string['current']
                        result[name]['Power'] = string['measured_power']
                        i += 1
        # Return result
        if jsonformat:
            return json.dumps(result, indent=4, sort_keys=True)
        else:
            return result

    # Pull Power Data
    def site(self, verbose=False):
        """ Grid Usage """
        return self.client.fetchpower('site', verbose)

    def solar(self, verbose=False):
        """" Solar Power Generation """
        return self.client.fetchpower('solar', verbose)

    def battery(self, verbose=False):
        """ Battery Power Flow """
        return self.client.fetchpower('battery', verbose)

    def load(self, verbose=False):
        """ Home Power Usage """
        return self.client.fetchpower('load', verbose)

    # Helpful Power Aliases
    def grid(self, verbose=False):
        """ Grid Power Usage """
        return self.site(verbose)

    def home(self, verbose=False):
        """ Home Power Usage """
        return self.load(verbose)

    # Shortcut Functions 
    def site_name(self) -> Optional[str]:
        """ System Site Name """
        payload = self.poll('/api/site_info/site_name')
        try:
            site_name = payload['site_name']
        except Exception as exc:
            log.debug(f"ERROR unable to parse payload '{payload}' for site_name: {exc}")
            site_name = None
        return site_name

    def status(self, param=None, jsonformat=False) -> Union[dict, str, None]:
        """ 
        Return Systems Status

        Args: 
          param = only respond with this param data
          jsonformat = If True, return JSON format otherwise return Python Dictionary

          Available param:
            din = payload['din']
            start_time = payload['start_time-time']
            up_time_seconds = payload['up_time_seconds']
            is_new = payload['is_new']
            version = payload['version']
            githash = payload['git_hash']
            commission_count = payload['commission_count']
            device_type = payload['device_type']
            sync_type = payload['sync_type']
            leader = payload['leader']
            followers = payload['followers']
            cellular_disabled = payload['cellular_disabled']
        """
        payload = self.client.poll('/api/status')
        if payload is None:
            return None
        if param is None:
            if jsonformat:
                return json.dumps(payload, indent=4, sort_keys=True)
            else:
                return payload
        else:
            if param in payload:
                return payload[param]
            else:
                log.debug('ERROR unable to find %s in payload: %r' % (param, payload))
                return None

    def version(self, int_value=False) -> Union[int, str, None]:
        """ Firmware Version """
        if not int_value:
            return self.status('version')
        # Convert version to integer
        return parse_version(self.status('version'))

    def uptime(self) -> Union[str, None]:
        """ System Uptime """
        return self.status('up_time_seconds')

    def din(self) -> Optional[str]:
        """ System DIN """
        return self.status('din')

    def temps(self, jsonformat=False) -> Optional[Union[dict, str]]:
        """
        Temperatures of Powerwalls

        Args:
          jsonformat = If True, return JSON format otherwise return Python Dictionary
        """
        temps = {}
        devices: dict = self.vitals() or {}
        for device in devices:
            if device.startswith('TETHC'):
                temps[device] = devices[device].get('THC_AmbientTemp')
        if jsonformat:
            return json.dumps(temps, indent=4, sort_keys=True)
        else:
            return temps

    def alerts(self, jsonformat=False, alertsonly=True) -> Union[list, str]:
        """ 
        Return Array of Alerts from all Devices 
        
        Args: 
          jsonformat = If True, return JSON format otherwise return Python Dictionary
          alertonly  = If True, return only alerts without device name
        """
        alerts = []
        devices: dict = self.vitals() or {}
        """
        The vitals API is not present in firmware versions > 23.44, this 
        is a workaround to get alerts from the /api/solar_powerwall endpoint
        for newer firmware versions
        """
        if devices:
            for device in devices:
                if 'alerts' in devices[device]:
                    for i in devices[device]['alerts']:
                        if alertsonly:
                            alerts.append(i)
                        else:
                            item = {device: i}
                            alerts.append(item)
        elif not devices and alertsonly is True:
            data: dict = self.poll('/api/solar_powerwall') or {}
            pvac_alerts = data.get('pvac_alerts') or {}
            for alert, value in pvac_alerts.items():
                if value is True:
                    alerts.append(alert)
            pvs_alerts = data.get('pvs_alerts') or {}
            for alert, value in pvs_alerts.items():
                if value is True:
                    alerts.append(alert)

        if jsonformat:
            return json.dumps(alerts, indent=4, sort_keys=True)
        else:
            return alerts

    def get_reserve(self, scale=True) -> Optional[float]:
        """
        Get Battery Reserve Percentage  
        Tesla App reserves 5% of battery => ( (batterylevel / 0.95) - (5 / 0.95) )

        Args:
            scale    = If True (default) use Tesla's 5% reserve calculation
        """
        data = self.poll('/api/operation')
        if data is not None and 'backup_reserve_percent' in data:
            percent = float(data['backup_reserve_percent'])
            if scale:
                # Get percentage based on Tesla App scale
                percent = float((percent / 0.95) - (5 / 0.95))
            return percent
        return None

    # noinspection PyShadowingBuiltins
    def grid_status(self, type="string") -> Optional[Union[str, int]]:
        """
        Get the status of the grid  
        
        Args:
            type == "string" (default) returns: "UP", "DOWN", "SYNCING"
            type == "json" return raw JSON
            type == "numeric" return -1 (Syncing), 0 (DOWN), 1 (UP)
        """
        if type not in ['json', 'string', 'numeric']:
            raise ValueError("Invalid value for parameter 'type': " + str(type))

        payload: dict = self.poll('/api/system_status/grid_status')

        if type == "json":
            return json.dumps(payload, indent=4, sort_keys=True)

        gridmap = {'SystemGridConnected': {'string': 'UP', 'numeric': 1},
                   'SystemIslandedActive': {'string': 'DOWN', 'numeric': 0},
                   'SystemTransitionToGrid': {'string': 'SYNCING', 'numeric': -1},
                   'SystemTransitionToIsland': {'string': 'SYNCING', 'numeric': -1},
                   'SystemIslandedReady': {'string': 'SYNCING', 'numeric': -1},
                   'SystemMicroGridFaulted': {'string': 'DOWN', 'numeric': 0},
                   'SystemWaitForUser': {'string': 'DOWN', 'numeric': 0}}

        grid_status = payload['grid_status']
        status = gridmap.get(grid_status, {}).get(type)
        if status is None:
            log.debug(f"ERROR unable to parse payload '{payload}' for grid_status of type: {type}")
        return status

    def system_status(self, jsonformat=False) -> Optional[Union[dict, str]]:
        """
        Get the full system status and basically do a straight passthrough return

        Some data points of note are
            nominal_full_pack_energy
            nominal_energy_remaining
            system_island_state - returns same value as grid_status
            available_blocks - number of batteries
            battery_blocks - array of batteries
                PackageSerialNumber
                disabled_reasons
                nominal_energy_remaining
                nominal_full_pack_energy
                v_out - voltage out
                f_out - frequency out
                energy_charged
                energy_discharged
                backup_ready
            grid_faults - array of faults

        Args:
            jsonformat = If True, return JSON format otherwise return Python Dictionary
        """
        payload: dict = self.poll('/api/system_status')
        if payload is None:
            return None

        if jsonformat:
            return json.dumps(payload, indent=4, sort_keys=True)
        else:
            return payload

    def battery_blocks(self, jsonformat=False):
        """
        Get detailed information about each battery. If you want to get aggregate power information 
        on all the batteries, use battery() 

        This function actually makes two API calls. The primary data is harvested from the 
        battery_blocks section in /api/system_status but the temperature data is only 
        available via /api/devices/vitals

        Some data points of note are
            battery_blocks - array of batteries
                disabled_reasons
                nominal_energy_remaining
                nominal_full_pack_energy
                v_out - voltage out
                f_out - frequency out
                energy_charged
                energy_discharged
                backup_ready
            grid_faults - array of faults

        Args:
            jsonformat = If True, return JSON format otherwise return Python Dictionary
        """
        system_status: dict = self.system_status()
        if system_status is None:
            return None

        devices: dict = self.vitals()
        if not devices:
            return None

        result = {}
        # copy the info from system_status into result
        # but change the key to the battery serial number
        for i in range(system_status['available_blocks']):
            bat = system_status['battery_blocks'][i]
            sn = bat['PackageSerialNumber']
            bat_res = {}
            for j in bat:
                if j != 'PackageSerialNumber':
                    bat_res[j] = bat[j]
            result[sn] = bat_res

        # now merge in the "interesting" data from vitals
        # Right now we're just pulling in the temp and state from the TETHC block
        # There is also info in TPOD and TINV that could be associated with the battery.
        for device in devices:
            if device.startswith("TETHC--"):
                sn = device.split("--")[2]
                bat_res = {
                    'THC_State': devices[device]['THC_State'],
                    'temperature': devices[device]['THC_AmbientTemp']
                }
                result[sn].update(bat_res)

        if jsonformat:
            return json.dumps(result, indent=4, sort_keys=True)
        else:
            return result

    def get_time_remaining(self) -> Optional[float]:
        """
        Get the backup time remaining on the battery

        Returns:
            The time remaining in hours
        """
        return self.client.get_time_remaining()
