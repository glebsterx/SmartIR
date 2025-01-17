import asyncio
import json
import logging
import os.path

import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerEntity, PLATFORM_SCHEMA)
from homeassistant.components.media_player.const import (
    SUPPORT_TURN_OFF, SUPPORT_TURN_ON, SUPPORT_PREVIOUS_TRACK,
    SUPPORT_NEXT_TRACK, SUPPORT_VOLUME_STEP, SUPPORT_VOLUME_MUTE, 
    SUPPORT_PLAY_MEDIA, SUPPORT_SELECT_SOURCE, MEDIA_TYPE_CHANNEL,
    SUPPORT_SELECT_SOUND_MODE)
from homeassistant.const import (
    CONF_NAME, STATE_OFF, STATE_ON, STATE_UNKNOWN)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity
from . import COMPONENT_ABS_DIR, Helper
from .controller import get_controller

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "SmartIR Media Player"
DEFAULT_DEVICE_CLASS = "tv"
DEFAULT_DELAY = 0.5

CONF_UNIQUE_ID = 'unique_id'
CONF_DEVICE_CODE = 'device_code'
CONF_CONTROLLER_DATA = "controller_data"
CONF_DELAY = "delay"
CONF_POWER_SENSOR = 'power_sensor'
CONF_RETAIN = 'retain'
CONF_SOUND_MODES = 'sound_modes'
CONF_SOURCE_NAMES = 'source_names'
CONF_DEVICE_CLASS = 'device_class'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_UNIQUE_ID): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_DEVICE_CODE): cv.positive_int,
    vol.Required(CONF_CONTROLLER_DATA): cv.string,
    vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.string,
    vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
    vol.Optional(CONF_RETAIN): list,
    vol.Optional(CONF_SOUND_MODES): dict,
    vol.Optional(CONF_SOURCE_NAMES): dict,
    vol.Optional(CONF_DEVICE_CLASS, default=DEFAULT_DEVICE_CLASS): cv.string
})

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the IR Media Player platform."""
    device_code = config.get(CONF_DEVICE_CODE)
    device_files_subdir = os.path.join('codes', 'media_player')
    device_files_absdir = os.path.join(COMPONENT_ABS_DIR, device_files_subdir)

    if not os.path.isdir(device_files_absdir):
        os.makedirs(device_files_absdir)

    device_json_filename = str(device_code) + '.json'
    device_json_path = os.path.join(device_files_absdir, device_json_filename)

    if not os.path.exists(device_json_path):
        _LOGGER.warning("Couldn't find the device Json file. The component will " \
                        "try to download it from the GitHub repo.")

        try:
            codes_source = ("https://raw.githubusercontent.com/"
                            "smartHomeHub/SmartIR/master/"
                            "codes/media_player/{}.json")

            await Helper.downloader(codes_source.format(device_code), device_json_path)
        except Exception:
            _LOGGER.error("There was an error while downloading the device Json file. " \
                          "Please check your internet connection or if the device code " \
                          "exists on GitHub. If the problem still exists please " \
                          "place the file manually in the proper directory.")
            return

    with open(device_json_path) as j:
        try:
            device_data = json.load(j)
        except Exception:
            _LOGGER.error("The device JSON file is invalid")
            return

    async_add_entities([SmartIRMediaPlayer(
        hass, config, device_data
    )])

class SmartIRMediaPlayer(MediaPlayerEntity, RestoreEntity):
    def __init__(self, hass, config, device_data):
        self.hass = hass
        self._unique_id = config.get(CONF_UNIQUE_ID)
        self._name = config.get(CONF_NAME)
        self._device_code = config.get(CONF_DEVICE_CODE)
        self._controller_data = config.get(CONF_CONTROLLER_DATA)
        self._delay = config.get(CONF_DELAY)
        self._power_sensor = config.get(CONF_POWER_SENSOR)
        self._retain = config.get(CONF_RETAIN, [])

        self._manufacturer = device_data['manufacturer']
        self._supported_models = device_data['supportedModels']
        self._supported_controller = device_data['supportedController']
        self._commands_encoding = device_data['commandsEncoding']
        self._commands = device_data['commands']

        self._state = STATE_OFF
        self._is_volume_muted = None
        self._sound_mode_list = []
        self._sound_mode = None
        self._sources_list_commands = {}
        self._sources_list = []
        self._source = None
        self._support_flags = 0

        self._device_class = config.get(CONF_DEVICE_CLASS)

        # Supported features
        if 'off' in self._commands and self._commands['off'] is not None:
            self._support_flags = self._support_flags | SUPPORT_TURN_OFF

        if 'on' in self._commands and self._commands['on'] is not None:
            self._support_flags = self._support_flags | SUPPORT_TURN_ON

        if 'previousChannel' in self._commands and self._commands['previousChannel'] is not None:
            self._support_flags = self._support_flags | SUPPORT_PREVIOUS_TRACK

        if 'nextChannel' in self._commands and self._commands['nextChannel'] is not None:
            self._support_flags = self._support_flags | SUPPORT_NEXT_TRACK

        if ('volumeDown' in self._commands and self._commands['volumeDown'] is not None) \
        or ('volumeUp' in self._commands and self._commands['volumeUp'] is not None):
            self._support_flags = self._support_flags | SUPPORT_VOLUME_STEP

        if 'mute' in self._commands and self._commands['mute'] is not None:
            self._support_flags = self._support_flags | SUPPORT_VOLUME_MUTE

        if 'sound_modes' in self._commands and self._commands['sound_modes'] is not None:
            
            for sound_mode, new_name in config.get(CONF_SOUND_MODES, {}).items():
                if sound_mode in self._commands['sound_modes']:
                    if new_name is not None:
                        self._commands['sound_modes'][new_name] = self._commands['sound_modes'][sound_mode]

                    del self._commands['sound_modes'][sound_mode]
            
            # Sound Modes list
            for key in self._commands['sound_modes']:
                self._sound_mode_list.append(key)
        
        if 'sources' in self._commands and self._commands['sources'] is not None:
            self._support_flags = self._support_flags | SUPPORT_SELECT_SOURCE | SUPPORT_PLAY_MEDIA

            self._sources_list_commands = self._commands['sources'].copy()
            
            for source, new_name in config.get(CONF_SOURCE_NAMES, {}).items():
                if source in self._sources_list_commands:
                    if new_name is not None:
                        self._sources_list_commands[new_name] = self._sources_list_commands[source]

                    del self._sources_list_commands[source]

            # Sources list
            for key in self._sources_list_commands:
                self._sources_list.append(key)

        self._temp_lock = asyncio.Lock()

        # Init the IR/RF controller
        self._controller = get_controller(
            self.hass,
            self._supported_controller, 
            self._commands_encoding,
            self._controller_data,
            self._delay)

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is not None:
            self._state = last_state.state
            self._is_volume_muted = last_state.attributes.get('is_volume_muted', None)
            self._sound_mode = last_state.attributes.get('sound_mode', None)
            self._source = last_state.attributes.get('source', None)
        
        if self._sound_mode_list and self._state == STATE_ON:
            self._support_flags |= SUPPORT_SELECT_SOUND_MODE

    @property
    def should_poll(self):
        """Push an update after each command."""
        return True

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the media player."""
        return self._name

    @property
    def device_class(self):
        """Return the device_class of the media player."""
        return self._device_class

    @property
    def state(self):
        """Return the state of the player."""
        return self._state

    @property
    def media_title(self):
        """Return the title of current playing media."""
        return None

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return MEDIA_TYPE_CHANNEL

    @property
    def is_volume_muted(self):
        return self._is_volume_muted

    @property
    def sound_mode_list(self):
        return self._sound_mode_list

    @property
    def sound_mode(self):
        return self._sound_mode

    @property
    def source_list(self):
        return self._sources_list
        
    @property
    def source(self):
        return self._source

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return self._support_flags

    @property
    def extra_state_attributes(self):
        """Platform specific attributes."""
        return {
            'device_code': self._device_code,
            'manufacturer': self._manufacturer,
            'supported_models': self._supported_models,
            'supported_controller': self._supported_controller,
            'commands_encoding': self._commands_encoding,
        }

    async def async_turn_off(self):
        """Turn the media player off."""
        await self.send_command(self._commands['off'])
        
        if self._power_sensor is None:
            self._state = STATE_OFF
            if 'mute' not in self._retain:
                self._is_volume_muted = None
            if 'sound_mode' not in self._retain:
                self._sound_mode = None
            if 'source' not in self._retain:
                self._source = None
            if self._sound_mode_list:
                self._support_flags ^= SUPPORT_SELECT_SOUND_MODE
            self.async_write_ha_state()

    async def async_turn_on(self):
        """Turn the media player off."""
        await self.send_command(self._commands['on'])

        if self._power_sensor is None:
            self._state = STATE_ON
            if self._sound_mode_list:
                self._support_flags |= SUPPORT_SELECT_SOUND_MODE
            self.async_write_ha_state()

    async def async_media_previous_track(self):
        """Send previous track command."""
        await self.send_command(self._commands['previousChannel'])
        self.async_write_ha_state()

    async def async_media_next_track(self):
        """Send next track command."""
        await self.send_command(self._commands['nextChannel'])
        self.async_write_ha_state()

    async def async_volume_down(self):
        """Turn volume down for media player."""
        await self.send_command(self._commands['volumeDown'])
        self.async_write_ha_state()

    async def async_volume_up(self):
        """Turn volume up for media player."""
        await self.send_command(self._commands['volumeUp'])
        self.async_write_ha_state()
    
    async def async_mute_volume(self, mute):
        """Mute the volume."""
        self._is_volume_muted = mute
        await self.send_command(self._commands['mute'])
        self.async_write_ha_state()

    async def async_select_sound_mode(self, sound_mode: str):
        """Select sound mode from list."""
        self._sound_mode = sound_mode
        await self.send_command(self._commands['sound_modes'][sound_mode])
        self.async_write_ha_state()

    async def async_select_source(self, source):
        """Select channel from source."""
        self._source = source
        await self.send_command(self._sources_list_commands[source])
        self.async_write_ha_state()

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Support channel change through play_media service."""
        if self._state == STATE_OFF:
            await self.async_turn_on()

        if media_type != MEDIA_TYPE_CHANNEL:
            _LOGGER.error("invalid media type")
            return
        if not media_id.isdigit():
            _LOGGER.error("media_id must be a channel number")
            return

        self._source = "Channel {}".format(media_id)
        for digit in media_id:
            await self.send_command(self._commands['sources']["Channel {}".format(digit)])
        self.async_write_ha_state()

    async def send_command(self, command):
        async with self._temp_lock:
            try:
                await self._controller.send(command)
            except Exception as e:
                _LOGGER.exception(e)
            
    async def async_update(self):
        if self._power_sensor is None:
            return

        power_state = self.hass.states.get(self._power_sensor)

        if power_state:
            if power_state.state == STATE_OFF:
                self._state = STATE_OFF
                if 'mute' not in self._retain:
                    self._is_volume_muted = None
                if 'sound_mode' not in self._retain:
                    self._sound_mode = None
                if 'source' not in self._retain:
                    self._source = None
                if self._sound_mode_list:
                    self._support_flags ^= SUPPORT_SELECT_SOUND_MODE
            elif power_state.state == STATE_ON:
                self._state = STATE_ON
                if self._sound_mode_list:
                    self._support_flags |= SUPPORT_SELECT_SOUND_MODE
