"use client"

import React, { useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'

export default function SyntheticV0PageForDeployment() {
  const [activeTab, setActiveTab] = useState('youtube')
  const [isProcessing, setIsProcessing] = useState(false)
  const [progress, setProgress] = useState(0)
  const [statusMessage, setStatusMessage] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [clips, setClips] = useState<any[]>([])

  useEffect(() => {
    // Status check interval
    let statusCheckInterval: NodeJS.Timeout | null = null

    if (isProcessing) {
      statusCheckInterval = setInterval(checkStatus, 2000)
    }

    return () => {
      if (statusCheckInterval) {
        clearInterval(statusCheckInterval)
      }
    }
  }, [isProcessing])

  const checkStatus = async () => {
    try {
      const response = await fetch('/status')
      const data = await response.json()

      setStatusMessage(data.message || 'Processing...')
      if (data.progress !== undefined) {
        setProgress(data.progress)
      }

      if (data.status === 'completed') {
        setIsProcessing(false)
        setClips(data.clips || [])
      } else if (data.status === 'error') {
        setIsProcessing(false)
        setError(data.error || 'An error occurred during processing')
      }
    } catch (error) {
      console.error('Error checking status:', error)
    }
  }

  const startProcessing = async (formData: any) => {
    try {
      setIsProcessing(true)
      setError(null)
      setClips([])

      const response = await fetch('/process', formData)
      const data = await response.json()

      if (data.error) {
        setError(data.error)
        setIsProcessing(false)
      }
    } catch (error) {
      setError('Failed to start processing: ' + (error as Error).message)
      setIsProcessing(false)
    }
  }

  return (
    <div className="container mx-auto px-4 py-8">
      <Card className="p-6">
        <Tabs defaultValue="youtube" className="w-full">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="youtube" onClick={() => setActiveTab('youtube')}>
              YouTube URL
            </TabsTrigger>
            <TabsTrigger value="upload" onClick={() => setActiveTab('upload')}>
              Upload Video
            </TabsTrigger>
          </TabsList>

          <TabsContent value="youtube">
            <form className="space-y-4" onSubmit={(e) => {
              e.preventDefault()
              const form = e.currentTarget
              const url = new FormData(form).get('youtubeUrl')?.toString() || ''
              
              if (!url) {
                setError('Please provide a YouTube URL')
                return
              }

              startProcessing({
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                  video_url: url,
                  language: 'English',
                  include_subtitles: true,
                  include_watermark: true,
                  watermark_text: '@clipah.com',
                  aspect_ratio: '9:16',
                }),
              })
            }}>
              <Input
                id="youtubeUrl"
                name="youtubeUrl"
                type="text"
                placeholder="Enter YouTube URL"
                required
              />
              <Button type="submit" disabled={isProcessing}>
                Generate Clips
              </Button>
            </form>
          </TabsContent>

          <TabsContent value="upload">
            <form className="space-y-4" onSubmit={(e) => {
              e.preventDefault()
              const form = e.currentTarget
              const file = new FormData(form).get('videoFile') as File

              if (!file) {
                setError('Please select a video file to upload')
                return
              }

              const formData = new FormData()
              formData.append('video_file', file)
              formData.append('language', 'English')
              formData.append('include_subtitles', 'true')
              formData.append('include_watermark', 'true')
              formData.append('watermark_text', '@clipah.com')
              formData.append('aspect_ratio', '9:16')

              startProcessing({
                method: 'POST',
                body: formData,
              })
            }}>
              <Input
                id="videoFile"
                name="videoFile"
                type="file"
                accept="video/*"
                required
              />
              <Button type="submit" disabled={isProcessing}>
                Upload & Generate Clips
              </Button>
            </form>
          </TabsContent>
        </Tabs>

        {isProcessing && (
          <div className="mt-8 space-y-4">
            <div className="h-2 bg-gray-200 rounded">
              <div
                className="h-full bg-blue-600 rounded transition-all duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-center text-gray-600">{statusMessage}</p>
          </div>
        )}

        {error && (
          <div className="mt-8 p-4 bg-red-100 text-red-700 rounded">
            {error}
          </div>
        )}

        {clips.length > 0 && (
          <div className="mt-8 space-y-6">
            <h2 className="text-2xl font-bold">Generated Clips</h2>
            {clips.map((clip, index) => (
              <Card key={index} className="p-4">
                <h3 className="text-lg font-semibold">{clip.clip_title}</h3>
                <p className="text-sm text-gray-600">
                  {clip.start_time} - {clip.end_time}
                </p>
                <p className="mt-2">{clip.summary}</p>
                {/* Video preview would go here */}
              </Card>
            ))}
            <Button onClick={() => window.location.href = '/download'}>
              Download All Clips
            </Button>
          </div>
        )}
      </Card>
    </div>
  )
}