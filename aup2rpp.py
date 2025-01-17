import struct
import xml.etree.ElementTree as ET
import uuid
import math
import pprint
import os
import html
import argparse

"""
shermnotes

.AU : A container format, used by Audacity for storage of lossless, uncompressed, 
PCM audio data. Not be confused with Sun/NeXT AU files, which are usually U-Law 
encoded PCM files but may be headerless. 

https://forum.audacityteam.org/viewtopic.php?t=73428

if converting to 16 bit then one should use dithering.

"""

#DONE: force 16-bit. currently jsut enable the flag and set noclip = False

#TODO: 24 bit support
#TODO: support weird sample rates if they arent already?
#TODO: auto save files only to 32 bit when the filehas peaks above 0db.


AU_SAMPLE_FORMAT_16 = 3
AU_SAMPLE_FORMAT_24 = 4
AU_SAMPLE_FORMAT_FLOAT = 6

IEEE_FLOAT = 3
PCM = 1

noclip = False #This feature isn't implemented properly yet. 
convert_to_16 = False #DEBUG #This feature isn't implemented properly yet. 
convert_to_32 = False #DEBUG #This feature isn't implemented properly yet. 

conversion_dict = {AU_SAMPLE_FORMAT_16:16, AU_SAMPLE_FORMAT_24:24, AU_SAMPLE_FORMAT_FLOAT:32}


def load_au_file(au_fpath):
	"""
	Returns dict of data info
	"""
	with open(au_fpath, 'rb') as f:

		# See https://github.com/audacity/audacity/blob/master/src/blockfile/SimpleBlockFile.cpp

		# wxUint32 magic;      // magic number
		# wxUint32 dataOffset; // byte offset to start of audio data
		# wxUint32 dataSize;   // data length, in bytes (optional)
		# wxUint32 encoding;   // data encoding enumeration
		# wxUint32 sampleRate; // samples per second
		# wxUint32 channels; // number of interleaved channels

		hcount = 6
		hdata = struct.unpack('I' * hcount, f.read(hcount * 4))

		result = {
			'magic': hdata[0],
			'data_offset': hdata[1],
			'data_size': hdata[2],
			'encoding': hdata[3],
			'sample_rate': hdata[4],
			'channels': hdata[5]
		}

		#print(result)

		if result['magic'] == 0x2e736e64:
			encoding = result['encoding']
		else:
			print("ERROR: Endianess needs to be swapped but I dunno what to do")
			return

		f.seek(result['data_offset'])

		ds = result['data_size']

		#if ds == 0xffffffff:
			# Size was specified as optional... read to end of file I guess?
			#ds = -1

		if encoding == AU_SAMPLE_FORMAT_16:
			sfc = 'h'
			ss = 2
		elif encoding == AU_SAMPLE_FORMAT_24:
			print("ERROR: 24-bit samples? Dunno how to read them")
			return
		elif encoding == AU_SAMPLE_FORMAT_FLOAT:
			sfc = 'f'
			ss = 4
		else:
			print("ERROR: I dunno this format ", encoding)
			return

		sample_data = []

		# Note: the file may be very big
		i = 0
		while i < ds:
			d = f.read(ss)
			if len(d) == 0:
				break
			sample_data.append(struct.unpack(sfc, d)[0])
			i += 1

	result['encoding'] = encoding #CLEANUP this seems redundant..?

	print('    ', result)

	result['sample_data'] = sample_data

	return result


class WavWriter:
	def __init__(self, f, sample_rate, channels, bits_per_sample):
		#WavWriter(f, au['sample_rate'], nchannels, 16 OR 32)
		self.f = f
		self.sample_rate = sample_rate
		self.channels = channels
		self.bits_per_sample = bits_per_sample

		self.finalized = False
		self.samples_count = 0

		self.fmt_chunk_size = 2 + 2 + 4 + 4 + 2 + 2

		self.initial_fpos = f.tell()

		if self.bits_per_sample == 32:
			self.type_of_format = IEEE_FLOAT
		else:
			self.type_of_format = PCM

		# Leave blank header size, we'll write it once all audio has been written.
		# Go straight to the offset where we will write samples
		riff_header_size = 8
		riff_chunk_size_without_data = 4 + (8 + self.fmt_chunk_size) + 8 + 0
		f.write(bytearray(riff_header_size + riff_chunk_size_without_data))

		self.data_fpos = f.tell()

	def append_multichannel_samples(self, sample_data_per_channel):
		assert not self.finalized
		assert self.channels == len(sample_data_per_channel)

		nchannels = self.channels

		if nchannels == 1:
			# We can take a shortcut
			interleaved_sample_data = sample_data_per_channel[0]
			max_sample_count = len(interleaved_sample_data)

		else:
			# Get max channel length
			max_sample_count = 0
			for sample_data in sample_data_per_channel:
				if len(sample_data) > max_sample_count:
					if max_sample_count != 0:
						# Ew, we had to adjust maximum twice
						print("WARNING: appending multichannel sample data with different amount of samples!")
					max_sample_count = len(sample_data)

			# Make sure all channels have the same size
			for sample_data in sample_data_per_channel:
				if len(sample_data) > max_sample_count:
					# Damn, where is resize(n)?
					del sample_data[-(len(sample_data) - max_sample_count):]
				else:
					while len(sample_data) < max_sample_count:
						sample_data.append(0)

			# Interleave
			interleaved_sample_data = [0] * (max_sample_count * nchannels)
			for channel, sample_data in enumerate(sample_data_per_channel):
				i = channel
				for v in sample_data:
					interleaved_sample_data[i] = v
					i += nchannels

		self.append_interleaved_samples(interleaved_sample_data)

	def append_interleaved_samples(self, sample_data):
		assert not self.finalized

		nsamples = len(sample_data) // self.channels
		assert nsamples * self.channels == len(sample_data)
		
		sfc = 'h'
		if self.bits_per_sample == 32:
			sfc = 'f'
 
		f = self.f
		#print(self.bits_per_sample) #DEBUG
		for v in sample_data:
			f.write(struct.pack(sfc, v))

		self.samples_count += nsamples

	def finalize(self): #sherman note: I think this is where the header is actually written.
		assert not self.finalized
		f = self.f

		end = f.tell()
		data_chunk_size = f.tell() - self.data_fpos
		f.seek(self.initial_fpos)

		assert data_chunk_size == (self.samples_count * self.channels * self.bits_per_sample // 8)
		# "WAVE" letters + two FourCC+size headers and their chunk size.
		# Does not include the size of the top-level header "RIFF"+size.
		riff_chunk_size = 4 + (8 + self.fmt_chunk_size) + (8 + data_chunk_size)

		f.write(b'RIFF')
		f.write(struct.pack('I', riff_chunk_size))

		f.write(b'WAVE')
		#wave_chunk_size = ???
		#f.write(struct.pack('I', wave_chunk_size))

		# ----------
		f.write(b'fmt ')
		f.write(struct.pack('I', self.fmt_chunk_size))

		# Format
		# PCM = 1 (i.e. Linear quantization) Values other than 1 indicate some form of compression.
		# IEEE float = 3
		f.write(struct.pack('H', self.type_of_format))

		f.write(struct.pack('H', self.channels))

		f.write(struct.pack('I', self.sample_rate))

		# SampleRate * NumChannels * BitsPerSample/8
		byte_rate = self.sample_rate * self.channels * self.bits_per_sample // 8
		f.write(struct.pack('I', byte_rate))

		# NumChannels * BitsPerSample/8
		block_align = self.channels * self.bits_per_sample // 8
		f.write(struct.pack('H', block_align))

		# 8 bits = 8, 16 bits = 16, etc.
		f.write(struct.pack('H', self.bits_per_sample))

		f.write(b'data')
		f.write(struct.pack('I', data_chunk_size))
		# And what follows is what we wrote before

		self.finalized = True


# Legacy shortcut
# def write_wav_file(fpath, sample_rate, channels, bits_per_sample, sample_data):
# 	with open(fpath, 'wb') as f:
# 		w = WavWriter(f, sample_rate, channels, bits_per_sample)
# 		w.append_samples(sample_data)
# 		w.finalize()


def convert_au_files_to_wav(src_paths_by_channel, dst_path):
	if len(src_paths_by_channel) == 0:
		return
	
	# Eliminate channels with no blocks
	temp = []
	for c in src_paths_by_channel:
		if len(c) != 0:
			temp.append(c)
	src_paths_by_channel = temp

	print("Converting blocks ", src_paths_by_channel)
	# Concatenate a bunch of .au block files into a single WAV file
	with open(dst_path, 'wb') as f:
		w = None

		nchannels = len(src_paths_by_channel)

		# For each block
		for block_index in range(len(src_paths_by_channel[0])):
			samples_by_channel = []

			# Process each corrsponding channel for that block
			for channel in range(nchannels):
				# this for loop gets an au block for each channel.
				src_paths = src_paths_by_channel[channel]

				if block_index >= len(src_paths):
					# That block doesn't have data on each channel...
					samples_by_channel.append([])
					continue

				au = load_au_file(src_paths[block_index])
				samples = au['sample_data'] #load samples

				if au['channels'] != 1:
					# TODO Deal with this eventually...
					# As far as I've seen, Audacity actually saves stereo blocks as separate mono .au files. WHY??
					print("ERROR: Unexpected AU file in stereo. Are you sure this is an audacity project?")
					return 0

				# Make sure it ends up in the encoding we want
				print(au['encoding']) #DEBUG
				if au['encoding'] == AU_SAMPLE_FORMAT_FLOAT:
					if convert_to_32:
						pass #it's already 32
					if convert_to_16:#converts 32-bit float to 16-bit PCM.
						#If samples clip don't convert. Unless noclip is off.
						if abs(max(samples, key=abs)) > 32767 or not noclip:
							for i, v in enumerate(samples):
								# convert to 16 bit PCM and clip any thing above 1 dB
								samples[i] = int(v * 32767.0)
								#clipping.
								samples[i] = min(samples[i], 32767) #too high
								samples[i] = max(samples[i], -32767) #too low

				elif au['encoding'] == AU_SAMPLE_FORMAT_24:
					#TODO: 24 bit support incl conversion
					print("ERROR: 24 bits not supported")
					return #TODO: this should be a proper error catch not a return IMO
				elif au['encoding'] == AU_SAMPLE_FORMAT_16:
					#if convert_to_32: #TODO
					pass # Already OK
				else:
					print("ERROR: Unknown .au encoding: ", au['encoding'])
					return 0 #this return 0 breaks it on purpose #TODO: proper error return


				#WavWriter calls
				if w is None: #if it's the first au written to the wav.
					if convert_to_16:
						w = WavWriter(f, au['sample_rate'], nchannels, 16) #no error here but still.
					else:
						w = WavWriter(f, au['sample_rate'], nchannels, conversion_dict[au['encoding']]) 
							#Encode to the existing encoding for the AU sample. This should probably have some weird error if the 
							#AU samples are concatenated to one wav and differ in encoding. But I don't think that should ever happen?
					#FIXME: feature: 16bit noclip. for this feature to work I need to check for 16bit vs 32 and make every au in that wav the same bitrate.
				if w.sample_rate != au['sample_rate']: #TODO understand how it works when it concatenates multiple AU files.
					print("ERROR: sample rate differs in one of the .au files I wanted to concatenate into one .wav")
					# TODO Return multiple files and split the clip...
					break

				samples_by_channel.append(samples) #add to list of list of samples.

			w.append_multichannel_samples(samples_by_channel) #Error here due to not writing to 32bit instead

		w.finalize()

	return 0 if w is None else w.samples_count


def load_audacity_project(fpath):
	root = ET.parse(fpath).getroot()

	rate = int(float(root.attrib["rate"]))
	name = root.attrib['projname']

	ns = { 'ns': 'http://audacity.sourceforge.net/xml/' }

	data_dir = os.path.splitext(fpath)[0] + '_data'
	if not os.path.isdir(data_dir):
		data_dir = ""

	def unescape(s):
		return html.unescape(s)

	output = {
		'rate': rate,
		'name': unescape(name),
		'data_dir': data_dir,
		'tracks': []
	}

	for project_item in root:
		tag = project_item.tag.split('}')[1]

		if tag == 'wavetrack':

			o_track = {
				'name': unescape(project_item.attrib['name']),
				'channel': int(project_item.attrib['channel']),
				'linked': True if project_item.attrib['linked'] == '1' else False,
				'mute': True if project_item.attrib['mute'] == '1' else False,
				'solo': True if project_item.attrib['solo'] == '1' else False,
				'rate': int(project_item.attrib['rate']),
				'gain': float(project_item.attrib['gain']),
				'pan': float(project_item.attrib['pan']),
				'color_index': int(project_item.attrib['colorindex']),
				'clips': []
			}

			output['tracks'].append(o_track)

			waveclips = project_item.findall('ns:waveclip', ns)

			for waveclip in waveclips:

				o_clip = {
					'offset': float(waveclip.attrib['offset']),
					'color_index': int(waveclip.attrib['colorindex']),
				}

				o_track['clips'].append(o_clip)

				sequence = waveclip.findall('ns:sequence', ns)[0]
				o_sequence = {
					'max_samples': int(sequence.attrib['maxsamples']),
					'sample_format': int(sequence.attrib['sampleformat']),
					'numsamples': int(sequence.attrib['numsamples']),
					'blocks': []
				}

				o_clip['sequence'] = o_sequence

				for waveblock in sequence.findall('ns:waveblock', ns):

					waveblock_start = int(waveblock.attrib['start'])

					for block in waveblock:
						btag = block.tag.split('}')[1]

						if btag == 'simpleblockfile':

							o_sequence['blocks'].append({
								'type': btag,
								'start': waveblock_start,
								'len': int(block.attrib['len']),
								'filename': unescape(block.attrib['filename']),
								'min': float(block.attrib['min']),
								'max': float(block.attrib['max']),
								'rms': float(block.attrib['rms']),
							})

						elif btag == 'pcmaliasblockfile':

							o_sequence['blocks'].append({
								'type': btag,
								'start': waveblock_start,
								'len': int(block.attrib['aliaslen']),
								'file_start': int(block.attrib['aliasstart']),
								'filename': unescape(block.attrib['aliasfile']),
								'summary_file': block.attrib['summaryfile'],
								'channel': int(block.attrib['aliaschannel']),
								'min': float(block.attrib['min']),
								'max': float(block.attrib['max']),
								'rms': float(block.attrib['rms'])
							})

						elif btag == 'silentblockfile':

							o_sequence['blocks'].append({
								'type': btag,
								'len': int(block.attrib['len'])
							})

						else:
							print("WARNING: Unknown block type: '{0}'".format(btag))

				envelope = waveclip.findall('ns:envelope', ns)[0]
				points = []
				for point in envelope.findall('ns:controlpoint', ns):
					points.append({
						't': float(point.attrib['t']),
						'val': float(point.attrib['val'])
					})

				o_clip['envelope'] = {
					'points': points
				}

	return output


def convert_au_files_from_audacity_project(project, target_dir):
	# This is where most of the conversion happens.

	indexed_files = {}

	if project['data_dir'] != "":
		# Audacity saves its media files under a nested hierarchy,
		# I don't quite understand why since files seem to have unique names
		for root, dirs, files in os.walk(project['data_dir']):
			for name in files:
				indexed_files[name] = os.path.join(root, name)

	if not os.path.isdir(target_dir):
		os.makedirs(target_dir)

	tracks = project['tracks']

	wavblock_history = {} # of form au_fpaths:(dst_fpath, converted_numsamples)
		#This stores hashes for sets of au files to prevent data duplication

	#TODO Eventually just make an entirely new project dictionary rather than modifying the input one
	converted_tracks = []
	project['converted_tracks'] = converted_tracks

	for track_index, track in enumerate(tracks):

		previous_track = None if track_index == 0 else tracks[track_index - 1]
		next_track = None if track_index + 1 == len(tracks) else tracks[track_index + 1]
		is_stereo_track = False

		if track['channel'] == 1:
			if previous_track is not None and previous_track['linked']:
				# Ignore second channel of a linked stereo track,
				# should be handled both in the previous iteration.
				# This means a converted project may have less tracks.
				continue

		elif track['channel'] == 0 and track['linked']:
			is_stereo_track = True

		converted_track = {
			'name': track['name'],
			'mute': track['mute'],
			'solo': track['solo'],
			'rate': track['rate'],
			'gain': track['gain'],
			'pan': track['pan'],
			'color_index': track['color_index'],
		}

		converted_tracks.append(converted_track)

		converted_clips = []
		converted_track['converted_clips'] = converted_clips

		for clip_index, clip in enumerate(track['clips']):

			sequence = clip['sequence']

			au_fpaths = [[], []]
			converted_numsamples = 0
			converted_clip_start = clip['offset'] # In seconds

			blocks = sequence['blocks']

			clip2 = None
			if is_stereo_track:
				clip2 = next_track['clips'][clip_index]
				if clip2['offset'] != clip['offset']:
					print("WARNING: Stereo track has non-aligned clips??")
					# Okayyy
					clip2 = None

			# Convert clip-wise envelopes into a track-wise one
			if len(clip['envelope']['points']) > 0:

				if 'envelope' not in converted_track:
					converted_envelope = { 'points': [] }
					converted_track['envelope'] = converted_envelope
				else:
					converted_envelope = converted_track['envelope']

				# Note: points will be sorted once we have gone through all clips
				points = clip['envelope']['points']
				for p in points:
					converted_envelope['points'].append({
						't': p['t'],
						'val': p['val']
					})

			# A clip can be made of many different blocks.
			# The goal is to process them in order to get one file per clip,
			# and then possibly splitting the clip or ignoring blocks.
			# Another fun part is joining stereo tracks,
			# because they are saved separately
			for block_index, block in enumerate(blocks):

				btype = block['type']
				is_last = block_index + 1 == len(blocks)
				is_next_different = not is_last and btype != blocks[block_index + 1]['type']

				if btype == 'simpleblockfile' or btype == 'pcmaliasblockfile':
					if converted_numsamples == 0:
						converted_clip_start = clip['offset'] + block['start'] / project['rate']
					converted_numsamples += block['len']

				if btype == 'simpleblockfile':# The files should probably end in au
					assert block['filename'].endswith('.au')

					block2 = None
					if is_stereo_track and clip2 is not None:
						for b in clip2['sequence']['blocks']:
							if b['start'] == block['start'] and b['len'] == block['len']:
								block2 = b
								break

					if block2 is not None:
						src_fpath = indexed_files[block['filename']]
						au_fpaths[0].append(src_fpath)
						src_fpath2 = indexed_files[block2['filename']]
						au_fpaths[1].append(src_fpath2)
					else:
						src_fpath = indexed_files[block['filename']]
						au_fpaths[0].append(src_fpath)

					if is_last or is_next_different: #stop grabbing new au files, we have ourselves a singel wav to make

						dst_fname = "track{0}_clip{1}.wav".format(track_index, len(converted_clips))
						dst_fpath = os.path.join(target_dir, dst_fname)

						#convert au_fpaths to a hashable object. of type tuple of tuple
						if type(au_fpaths) is list:
							au_fpaths = tuple(tuple(i) for i in au_fpaths)

						if au_fpaths not in wavblock_history.keys():
							if os.path.isfile(dst_fpath):
								print("Overwriting ", dst_fpath)
							samples_in_file = convert_au_files_to_wav(au_fpaths, dst_fpath)
							# Check this because there is redundancy, I'm curious if that can fail
							if samples_in_file != converted_numsamples:
								print("WARNING: Sample count mismatch between what I found in the .aup and the actual files")
								print("         .aup: {0}, file: {1}".format(total_samples, converted_numsamples))

						else:
							print('Repeat found! Memory saved :)', wavblock_history[au_fpaths][0]) #duplicate sounds like an error. say Repeat. Not an Error
							if samples_in_file != converted_numsamples:
								print("WARNING: Sample count mismatch between source and repeat") #If this ever happens then my trick doesnt work.
								print("         saved wav: {0}, repeat clip start: {1}".format(wavblock_history[au_fpaths][0], converted_clip_start))

						#Try to not duplicate files when the .au was re-used.
						# We could do this by hashing au_fpaths, and if it's the same then use existing result
						
						wavblock_history.setdefault(au_fpaths, (dst_fpath, converted_numsamples))



						converted_clips.append({
							'offset': converted_clip_start,
							'numsamples': wavblock_history[au_fpaths][1], #numsamples should be the same for duplicates... ! think!
							'filename': wavblock_history[au_fpaths][0]
						})

						au_fpaths = [[], []]
						converted_numsamples = 0

				elif btype == 'pcmaliasblockfile':
					# We don't do anything special regarding stereo, the source file should be fine already

					if not is_last:
						next_block = blocks[block_index + 1]
						if next_block['type'] == 'pcmaliasblockfile':
							if next_block['filename'] != block['filename']:
								is_next_different = True

					if is_last or is_next_different:
						converted_clips.append({
							'offset': converted_clip_start,
							'numsamples': converted_numsamples,
							'filename': block['filename'],
							'file_start': block['file_start']
						})

						converted_numsamples = 0

				elif btype == 'silentblockfile':
					pass # Ignore

				else:
					print("WARNING: Unsupported block type: '{0}'".format(btype))

		# Reorder envelope points by time
		if 'envelope' in converted_track:
			envelope = converted_track['envelope']
			envelope['points'] = sorted(envelope['points'], key=lambda x: x['t'])


def write_rpp_file_from_audacity_project(fpath, project):

	audacity_color_to_peakcol = [
		0, # 0: Default color in Audacity (blue)
		0x013333ff, # 1: Red
		0x0133ff33, # 2: Green
		0x01222222 # 3: Black
	]

	def get_file_tag(fname):
		ext = os.path.splitext(fname)[1]
		if ext == '.wav':
			return 'WAVE'
		elif ext == 'ogg':
			return 'VORBIS'
		return ext[1:].upper()

	# Audacity saves gain as a linear value, and it turns out Reaper also does
	# def linear2db(p_linear)
	# 	return math.log(p_linear) * 8.6858896380650365530225783783321

	class RppWriter:
		def __init__(self, f):
			self.indent_unit = "  "
			self.indent = ""
			self.f = f

		def open_block(self, tag, *args):
			self.f.write('{0}<{1}'.format(self.indent, tag))
			self._args(args)
			self.indent += self.indent_unit

		def close_block(self):
			self.indent = self.indent[:-len(self.indent_unit)]
			self.f.write('{0}>\n'.format(self.indent))

		def line(self, tag, *args):
			self.f.write('{0}{1}'.format(self.indent, tag))
			self._args(args)

		def _args(self, args):
			for v in args:
				if type(v) == str:
					s = ' "{0}"'# if v.contains(' ') else ' {0}'
					self.f.write(s.format(v))
				elif type(v) == bool:
					self.f.write(' {0}'.format(1 if v else 0))
				elif type(v) == uuid.UUID:
					self.f.write(' {' + str(v).upper() + '}')
				else:
					self.f.write(' ' + str(v))
			self.f.write('\n')

	# One nice thing about Reaper projects is that you can omit things in it,
	# it will not complain and just load what it finds, apparently

	with open(fpath, 'w', encoding="utf-8") as f:
		w = RppWriter(f)

		# Arbitrary version, which happens to be mine at time of writing. #3 years old, but we'll keep it to not break things...
		# TODO I don't know what the number at the end is
		w.open_block('REAPER_PROJECT', 0.1, '5.92/x64', 1534982487)

		project_samplerate = int(project['rate'])
		w.line('SAMPLERATE', project_samplerate, 0, 0)

		for track in project['converted_tracks']:

			track_uid = uuid.uuid4()

			w.open_block('TRACK', track_uid)

			w.line('NAME', track['name'])
			w.line('TRACKID', track_uid)
			w.line('VOLPAN', track['gain'], track['pan'], -1, -1, 1)
			w.line('NCHAN', 2)
			w.line('MUTESOLO', track['mute'], track['solo'])
			w.line('PEAKCOL', audacity_color_to_peakcol[track['color_index']])

			if 'envelope' in track:
				w.open_block('VOLENV2')

				for point in track['envelope']['points']:
					w.line('PT', point['t'], point['val'])

				w.close_block()

			for clip in track['converted_clips']:

				w.open_block('ITEM')

				w.line('POSITION', clip['offset'])
				# TODO I don't know what these UIDs are
				w.line('IGUID', uuid.uuid4())
				w.line('GUID', uuid.uuid4())
				w.line('NAME', os.path.basename(clip['filename']))

				nsamples = clip['numsamples']
				item_len_seconds = nsamples / project_samplerate

				w.line('LENGTH', item_len_seconds)

				if 'file_start' in clip:
					w.line('SOFFS', clip['file_start'] / project_samplerate)
				
				w.open_block('SOURCE ' + get_file_tag(clip['filename']))
				w.line('FILE', clip['filename'])
				w.close_block()

				# Note: sources like this can exist:
				# <SOURCE SECTION
				#   LENGTH 3.55565072008221
				#   STARTPOS 7.40378238649376
				#   OVERLAP 0.01
				#   <SOURCE FLAC
				#     FILE "D:\PROJETS\AUDIO\coproductions\1287\Episodes\Episode 7\foule_armee.flac"
				#   >
				# >

				#NOTE: would it be possible to create a reaper file which just links to AU files? much faster..
				w.close_block()

			w.close_block()

		w.close_block()


def convert(aup_path):
	project = load_audacity_project(aup_path)
	# pp = pprint.PrettyPrinter(indent=4)
	# pp.pprint(project)
	# return

	data_dir = os.path.splitext(aup_path)[0] + '_wav_data'
	convert_au_files_from_audacity_project(project, data_dir)

	rpp_path = os.path.splitext(aup_path)[0] + '.rpp'
	write_rpp_file_from_audacity_project(rpp_path, project)

	print("Done")


if __name__ == '__main__':

	parser = argparse.ArgumentParser(description='Converts Audacity projects into Reaper projects.')

	parser.add_argument('audacity_project', metavar='audacity_project', type=str, 
		help='Path to the Audacity project to convert (.aup file)')

	#TODO: parser output filename
	#parser.add_argument('output_project', type=str,)

	#parser: --bitrate [auto 16 float ]
	parser.add_argument('--force-encoding', type=str, help='force all audio to be encoded as {16,32} bit wav files. If not specified, encoding is flexible.')
	parser.add_argument('--dont-clip', type=str, help='if transcoding lower causes clipping, don\' transcode it')

	args = parser.parse_args()

	convert(args.audacity_project)
