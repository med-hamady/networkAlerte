'use client'

import React from 'react'
import FaiHistory from '@/components/FaiHistory'

export default function FaiRequestsPage() {
  return <div className="space-y-6">
    <h1 className="text-2xl font-bold text-blue-900">Demandes de coupure</h1>
    <FaiHistory />
  </div>
}
